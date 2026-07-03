"""
white_salary/core/plugins/developer.py

开发者平台 — 三级权限账号系统。

角色：
  - super_admin: 超级管理员（只有一个，不可删除）
  - admin: 管理员（超级管理员指定）
  - developer: 开发者（注册+审批）

流程：
  - 注册 → pending → 管理员审批 → approved → 可提交插件
  - 密码SHA256加密存储
  - 登录发token，24小时过期
"""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


# Token有效期（秒）
TOKEN_EXPIRE_SECONDS = 86400  # 24小时

# 2026-07-03 面板升级（批6）：默认超管密码提为常量——登录时用它检测
# "仍在用默认密码"并提示修改（见 panel-developer.json 安全项）
DEFAULT_SUPER_ADMIN_PASSWORD = "whitesalary2026"


@dataclass
class Developer:
    """开发者信息。"""
    username: str = ""
    password_hash: str = ""
    role: str = "developer"      # super_admin / admin / developer
    status: str = "pending"      # pending / approved / rejected
    created_at: str = ""
    plugins_submitted: list = None

    def __post_init__(self):
        if self.plugins_submitted is None:
            self.plugins_submitted = []


class DeveloperManager:
    """
    开发者管理器。

    使用方式:
        dm = DeveloperManager()
        dm.register("xiaoming", "password123")
        token = dm.login("xiaoming", "password123")
        dm.approve("xiaoming", admin_token="xxx")
    """

    def __init__(self, config_dir: str = "config") -> None:
        self._path = Path(config_dir) / "developers.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._developers: dict[str, dict] = {}
        self._tokens: dict[str, dict] = {}  # token → {username, expires_at}
        self._load()
        self._ensure_super_admin()

    # ================================================================
    # 注册/登录
    # ================================================================

    def register(self, username: str, password: str) -> dict:
        """注册开发者账号（需管理员审批后才能用）。"""
        if not username or not password:
            return {"success": False, "message": "用户名和密码不能为空"}
        if len(username) < 2 or len(username) > 20:
            return {"success": False, "message": "用户名2-20字符"}
        if len(password) < 6:
            return {"success": False, "message": "密码至少6位"}
        if username in self._developers:
            return {"success": False, "message": "用户名已存在"}

        self._developers[username] = asdict(Developer(
            username=username,
            password_hash=self._hash_password(password),
            role="developer",
            status="pending",
            created_at=time.strftime("%Y-%m-%d %H:%M"),
        ))
        self._save()
        logger.info(f"[Developer] 新注册: {username} (待审批)")
        return {"success": True, "message": "注册成功，等待管理员审批"}

    def login(self, username: str, password: str) -> dict:
        """登录，返回token。"""
        dev = self._developers.get(username)
        if not dev:
            return {"success": False, "message": "用户不存在"}
        # 2026-07-03 面板升级（批6）：GitHub 同步改为公开视图后，远端拉回的账号
        # 可能没有 password_hash 字段——用 get 取值防 KeyError（哈希缺失时任何
        # 密码都不匹配，不会造成空密码放行）
        if dev.get("password_hash", "") != self._hash_password(password):
            return {"success": False, "message": "密码错误"}
        if dev["status"] != "approved":
            return {"success": False, "message": f"账号状态: {dev['status']}，需管理员审批"}

        # 生成token
        token = secrets.token_hex(32)
        self._tokens[token] = {
            "username": username,
            "expires_at": time.time() + TOKEN_EXPIRE_SECONDS,
        }
        self._save()
        result = {
            "success": True,
            "token": token,
            "username": username,
            "role": dev["role"],
            "expires_in": TOKEN_EXPIRE_SECONDS,
        }
        # 2026-07-03 面板升级（批6）：命中默认密码时提示尽快修改
        # （默认密码写在源码里等于公开，见 panel-developer.json 安全项）
        if dev.get("password_hash", "") == self._hash_password(DEFAULT_SUPER_ADMIN_PASSWORD):
            result["must_change_password"] = True
            result["message"] = (
                "当前仍在使用默认密码，请尽快在 conf.yaml 的 admin.password "
                "中设置新密码并重启"
            )
        return result

    def verify_token(self, token: str) -> Optional[dict]:
        """验证token，返回用户信息或None。"""
        info = self._tokens.get(token)
        if not info:
            return None
        if time.time() > info["expires_at"]:
            del self._tokens[token]
            self._save()
            return None
        username = info["username"]
        dev = self._developers.get(username)
        if not dev:
            return None
        return {"username": username, "role": dev["role"]}

    def logout(self, token: str) -> dict:
        """登出（删除token）。"""
        if token in self._tokens:
            del self._tokens[token]
            self._save()
        return {"success": True}

    # ================================================================
    # 管理员操作
    # ================================================================

    def approve(self, username: str, operator_token: str) -> dict:
        """审批开发者（需管理员权限）。"""
        op = self._check_admin(operator_token)
        if not op:
            return {"success": False, "message": "需要管理员权限"}
        dev = self._developers.get(username)
        if not dev:
            return {"success": False, "message": "用户不存在"}
        dev["status"] = "approved"
        self._save()
        logger.info(f"[Developer] {op['username']} 审批了 {username}")
        return {"success": True, "message": f"已审批 {username}"}

    def reject(self, username: str, operator_token: str) -> dict:
        """拒绝开发者。"""
        op = self._check_admin(operator_token)
        if not op:
            return {"success": False, "message": "需要管理员权限"}
        if username in self._developers:
            del self._developers[username]
            self._save()
        return {"success": True, "message": f"已拒绝 {username}"}

    def set_admin(self, username: str, operator_token: str) -> dict:
        """设为管理员（需超级管理员权限）。"""
        op = self._check_super_admin(operator_token)
        if not op:
            return {"success": False, "message": "需要超级管理员权限"}
        dev = self._developers.get(username)
        if not dev:
            return {"success": False, "message": "用户不存在"}
        dev["role"] = "admin"
        dev["status"] = "approved"
        self._save()
        logger.info(f"[Developer] {username} 被设为管理员")
        return {"success": True}

    def remove_admin(self, username: str, operator_token: str) -> dict:
        """取消管理员（需超级管理员权限）。"""
        op = self._check_super_admin(operator_token)
        if not op:
            return {"success": False, "message": "需要超级管理员权限"}
        dev = self._developers.get(username)
        if not dev:
            return {"success": False, "message": "用户不存在"}
        if dev["role"] == "super_admin":
            return {"success": False, "message": "不能取消超级管理员"}
        dev["role"] = "developer"
        self._save()
        return {"success": True}

    def delete_developer(self, username: str, operator_token: str) -> dict:
        """删除开发者（管理员可删开发者，超管可删管理员）。"""
        op = self.verify_token(operator_token)
        if not op:
            return {"success": False, "message": "token无效"}

        dev = self._developers.get(username)
        if not dev:
            return {"success": False, "message": "用户不存在"}

        # 超级管理员不可删
        if dev["role"] == "super_admin":
            return {"success": False, "message": "不能删除超级管理员"}
        # 管理员只能被超管删
        if dev["role"] == "admin" and op["role"] != "super_admin":
            return {"success": False, "message": "只有超级管理员能删管理员"}
        # 普通开发者需要管理员权限删
        if op["role"] not in ("admin", "super_admin"):
            return {"success": False, "message": "需要管理员权限"}

        del self._developers[username]
        # 清理该用户的token
        self._tokens = {k: v for k, v in self._tokens.items() if v["username"] != username}
        self._save()
        return {"success": True}

    def list_developers(self) -> list[dict]:
        """列出所有开发者。"""
        result = []
        for username, dev in self._developers.items():
            result.append({
                "username": username,
                "role": dev["role"],
                "status": dev["status"],
                "created_at": dev.get("created_at", ""),
                "plugins_count": len(dev.get("plugins_submitted", [])),
            })
        return result

    # ================================================================
    # 权限检查
    # ================================================================

    def can_edit_plugin(self, token: str, plugin_author: str) -> bool:
        """检查是否有权编辑某个插件。"""
        user = self.verify_token(token)
        if not user:
            return False
        # 管理员能编辑所有
        if user["role"] in ("admin", "super_admin"):
            return True
        # 开发者只能编辑自己的
        return user["username"] == plugin_author

    def _check_admin(self, token: str) -> Optional[dict]:
        """检查是否是管理员或超管。"""
        user = self.verify_token(token)
        if user and user["role"] in ("admin", "super_admin"):
            return user
        return None

    def _check_super_admin(self, token: str) -> Optional[dict]:
        """检查是否是超级管理员。"""
        user = self.verify_token(token)
        if user and user["role"] == "super_admin":
            return user
        return None

    # ================================================================
    # 初始化
    # ================================================================

    def _ensure_super_admin(self) -> None:
        """确保有一个超级管理员。"""
        has_super = any(
            d.get("role") == "super_admin"
            for d in self._developers.values()
        )

        # 从conf.yaml读取或用默认
        # 2026-07-03 面板升级（批6）：默认密码改用模块常量（登录检测复用同一来源）
        username = "admin"
        password = DEFAULT_SUPER_ADMIN_PASSWORD
        try:
            import yaml
            conf_path = Path("conf.yaml")
            if conf_path.exists():
                conf = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
                username = conf.get("admin", {}).get("username", username)
                password = conf.get("admin", {}).get("password", password)
        except Exception:
            pass

        if not has_super:
            self._developers[username] = asdict(Developer(
                username=username,
                password_hash=self._hash_password(password),
                role="super_admin",
                status="approved",
                created_at=time.strftime("%Y-%m-%d %H:%M"),
            ))
            self._save()
            logger.info(f"[Developer] 初始超级管理员已创建: {username}")
            return

        # 2026-07-03 面板升级（批6）：GitHub 公开视图拉回的超管可能没有密码哈希
        # 且本地缓存也没有——此时按 conf/默认密码补回哈希，避免超管永久锁死
        for dev in self._developers.values():
            if dev.get("role") == "super_admin" and not dev.get("password_hash"):
                dev["password_hash"] = self._hash_password(password)
                self._save()
                logger.warning(
                    "[Developer] 超级管理员缺失密码哈希（远端公开视图），"
                    "已按 conf.yaml admin.password / 默认密码重置"
                )

    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _hash_password(password: str) -> str:
        """SHA256加密密码。"""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def _save(self) -> None:
        """保存到GitHub + 本地缓存。"""
        data = {
            "developers": self._developers,
            "tokens": self._tokens,
        }
        # 本地缓存（快速读取用）
        try:
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # 同步到GitHub（异步后台执行，不阻塞）
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._save_to_github(data))
            else:
                loop.run_until_complete(self._save_to_github(data))
        except RuntimeError:
            # 没有event loop时只存本地
            pass

    @staticmethod
    def _build_public_sync_payload(data: dict) -> dict:
        """
        2026-07-03 面板升级（批6）：构造GitHub同步用的公开视图载荷。

        只同步用户名/角色/状态等公开字段，剔除 tokens 整节与每个账号的
        password_hash——此前全量明文同步到公共插件仓库，等于把全部登录态
        和密码哈希公开（见 panel-developer.json 安全项）。

        参数:
            data: {"developers": {...}, "tokens": {...}} 全量数据

        返回:
            {"developers": {username: 公开字段dict}}（不含 tokens 键）
        """
        public: dict[str, dict] = {}
        for username, dev in (data.get("developers") or {}).items():
            if not isinstance(dev, dict):
                continue
            public[username] = {
                "username": dev.get("username", username),
                "role": dev.get("role", "developer"),
                "status": dev.get("status", "pending"),
                "created_at": dev.get("created_at", ""),
                "plugins_submitted": list(dev.get("plugins_submitted") or []),
            }
        return {"developers": public}

    async def _save_to_github(self, data: dict) -> None:
        """保存developers.json到GitHub仓库（只同步公开视图）。"""
        try:
            import aiohttp, base64
            gc = self._load_github_config()
            if not gc.get("token"):
                return

            token = gc["token"]
            repo = gc.get("repo", "VBHC-UHY/whitesalary-plugins")
            url = f"https://api.github.com/repos/{repo}/contents/developers.json"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }

            # 2026-07-03 面板升级（批6）：同步内容改为公开视图（无tokens/密码哈希）
            payload = self._build_public_sync_payload(data)

            async with aiohttp.ClientSession() as session:
                # 获取SHA
                sha = None
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        d = await resp.json()
                        sha = d.get("sha")

                # 写入
                content = base64.b64encode(
                    json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                ).decode()
                body = {"message": "Update developers", "content": content}
                if sha:
                    body["sha"] = sha
                async with session.put(url, headers=headers, json=body) as resp:
                    if resp.status in (200, 201):
                        logger.debug("[Developer] GitHub同步成功")
                    else:
                        logger.debug(f"[Developer] GitHub同步失败: {resp.status}")
        except Exception as e:
            logger.debug(f"[Developer] GitHub同步异常: {e}")

    def _load(self) -> None:
        """从GitHub加载（失败则用本地缓存）。"""
        # 先尝试从GitHub读
        loaded_from_github = False
        try:
            import urllib.request, base64
            gc = self._load_github_config()
            if gc.get("token"):
                repo = gc.get("repo", "VBHC-UHY/whitesalary-plugins")
                url = f"https://api.github.com/repos/{repo}/contents/developers.json"
                req = urllib.request.Request(url, headers={
                    "Authorization": f"token {gc['token']}",
                    "Accept": "application/vnd.github.v3+json",
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    content = base64.b64decode(data["content"]).decode("utf-8")
                    parsed = json.loads(content)
                    self._developers = parsed.get("developers", {})
                    self._tokens = parsed.get("tokens", {})
                    loaded_from_github = True
                    logger.debug("[Developer] 从GitHub加载成功")
        except Exception as e:
            logger.debug(f"[Developer] GitHub加载失败: {e}")

        # GitHub失败则用本地缓存
        if not loaded_from_github and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._developers = data.get("developers", {})
                self._tokens = data.get("tokens", {})
                logger.debug("[Developer] 从本地缓存加载")
            except Exception:
                pass

        # 2026-07-03 面板升级（批6）：GitHub 侧现在只存公开视图（无密码哈希、
        # 无tokens）——从远端加载成功后，用本地缓存回填敏感字段，否则所有账号
        # 都会因哈希缺失而无法登录、全部会话丢失
        if loaded_from_github and self._path.exists():
            try:
                local = json.loads(self._path.read_text(encoding="utf-8"))
                local_devs = local.get("developers", {})
                for username, dev in self._developers.items():
                    if not dev.get("password_hash"):
                        cached = local_devs.get(username) or {}
                        if cached.get("password_hash"):
                            dev["password_hash"] = cached["password_hash"]
                # 远端公开视图没有tokens节；不采信远端token（防泄露token直接可用），
                # 会话一律以本地缓存为准
                if not self._tokens:
                    self._tokens = local.get("tokens", {})
            except Exception as e:
                logger.debug(f"[Developer] 本地敏感字段回填失败: {e}")

        # 清理过期token
        now = time.time()
        self._tokens = {
            k: v for k, v in self._tokens.items()
            if v.get("expires_at", 0) > now
        }

    @staticmethod
    def _load_github_config() -> dict:
        """读取GitHub配置。"""
        try:
            gc_path = Path("config/github_config.json")
            if gc_path.exists():
                return json.loads(gc_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    @property
    def stats(self) -> dict:
        return {
            "total": len(self._developers),
            "approved": sum(1 for d in self._developers.values() if d.get("status") == "approved"),
            "pending": sum(1 for d in self._developers.values() if d.get("status") == "pending"),
            "admins": sum(1 for d in self._developers.values() if d.get("role") in ("admin", "super_admin")),
        }
