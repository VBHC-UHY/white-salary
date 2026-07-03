"""
white_salary/core/memory/user_filter.py

用户过滤器 — 白名单/黑名单+自动拉黑恶意用户。

借鉴v2的features/user_filter.py（563行）：
  - 三种模式：白名单/黑名单/关闭
  - 软拉黑（24小时过期）+硬拉黑（永久）
  - 3次违规自动升级为永久拉黑
  - 主人永远免检
  - 新用户第一条消息可用detect_llm评估

LLM通道：detect_llm（仅新用户首次评估时用）

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class BlacklistEntry:
    """黑名单条目。"""
    user_id: str = ""
    nickname: str = ""
    reason: str = ""
    added_time: float = 0.0
    added_by: str = "auto"        # manual/auto
    expires_at: float = 0.0       # 0=永久
    strike_count: int = 0         # 违规次数


class FilterMode:
    OFF = "off"                   # 不过滤
    WHITELIST = "whitelist"       # 只响应白名单
    BLACKLIST = "blacklist"       # 不响应黑名单


class FilterResult:
    ALLOW = "allow"
    BLOCK = "block"
    DETECT = "detect"             # 需要LLM评估


DEFAULT_SOFT_EXPIRE_HOURS = 24
HARD_BLACKLIST_THRESHOLD = 3      # 3次软拉黑→永久


class UserFilter:
    """
    用户过滤器。

    使用方式:
        f = UserFilter(data_dir, owner_id="999")
        result = f.check("123")
        if result == FilterResult.BLOCK:
            # 不响应
    """

    def __init__(self, data_dir: str = "data/memory", owner_id: str = "") -> None:
        self._data_path = Path(data_dir) / "user_filter.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_id = owner_id

        self._mode: str = FilterMode.BLACKLIST  # 默认黑名单模式
        self._whitelist: set[str] = set()
        self._hard_blacklist: dict[str, BlacklistEntry] = {}
        self._soft_blacklist: dict[str, BlacklistEntry] = {}
        self._verified: set[str] = set()  # 已验证安全的用户

        self._load()

    def check(self, user_id: str) -> str:
        """
        检查用户是否允许。

        Returns:
            FilterResult.ALLOW / BLOCK / DETECT
        """
        # 主人永远免检
        if user_id == self._owner_id:
            return FilterResult.ALLOW

        if self._mode == FilterMode.OFF:
            return FilterResult.ALLOW

        if self._mode == FilterMode.WHITELIST:
            return FilterResult.ALLOW if user_id in self._whitelist else FilterResult.BLOCK

        # 黑名单模式
        # 硬拉黑
        if user_id in self._hard_blacklist:
            return FilterResult.BLOCK

        # 软拉黑（检查是否过期）
        if user_id in self._soft_blacklist:
            entry = self._soft_blacklist[user_id]
            if entry.expires_at > 0 and time.time() > entry.expires_at:
                # 过期了，移除
                del self._soft_blacklist[user_id]
                self._save()
            else:
                return FilterResult.BLOCK

        # 好感度自动拉黑检查
        affinity_result = self._check_affinity_blacklist(user_id)
        if affinity_result == FilterResult.BLOCK:
            return FilterResult.BLOCK

        # 已验证
        if user_id in self._verified:
            return FilterResult.ALLOW

        return FilterResult.ALLOW

    def add_to_blacklist(self, user_id: str, nickname: str = "",
                         reason: str = "", permanent: bool = False,
                         expire_hours: int = DEFAULT_SOFT_EXPIRE_HOURS) -> None:
        """拉黑用户。"""
        if user_id == self._owner_id:
            return  # 不能拉黑主人

        if permanent:
            self._hard_blacklist[user_id] = BlacklistEntry(
                user_id=user_id, nickname=nickname, reason=reason,
                added_time=time.time(), added_by="manual", expires_at=0,
            )
            logger.info(f"[UserFilter] 永久拉黑: {nickname}({user_id}) 原因: {reason}")
        else:
            # 检查是否已有软拉黑记录
            existing = self._soft_blacklist.get(user_id)
            strike = (existing.strike_count + 1) if existing else 1

            if strike >= HARD_BLACKLIST_THRESHOLD:
                # 自动升级为永久
                self._hard_blacklist[user_id] = BlacklistEntry(
                    user_id=user_id, nickname=nickname,
                    reason=f"累计{strike}次违规自动永久拉黑",
                    added_time=time.time(), added_by="auto", expires_at=0,
                    strike_count=strike,
                )
                self._soft_blacklist.pop(user_id, None)
                logger.warning(f"[UserFilter] {nickname}({user_id}) 累计{strike}次→永久拉黑")
            else:
                self._soft_blacklist[user_id] = BlacklistEntry(
                    user_id=user_id, nickname=nickname, reason=reason,
                    added_time=time.time(), added_by="auto",
                    expires_at=time.time() + expire_hours * 3600,
                    strike_count=strike,
                )
                logger.info(f"[UserFilter] 软拉黑: {nickname}({user_id}) "
                            f"第{strike}次，{expire_hours}小时后解除")
        self._save()

    def list_blacklist(self) -> list[dict]:
        """
        2026-07-03 面板升级（批6）：黑名单明细公开方法。

        供设置面板"黑名单查看/解除"渲染（此前 GET /users/filter 只回计数，
        拉黑了谁在面板上完全看不见，见 panel-users.json"黑名单查看/移除"）。

        返回:
            [{user_id, nickname, reason, added_time, added_by, expires_at,
              strike_count, type}]，type 为 "hard"(永久)/"soft"(限时)；
            已过期的软拉黑条目不返回。按拉黑时间倒序排列。
        """
        now = time.time()
        result: list[dict] = []
        for entry in self._hard_blacklist.values():
            item = asdict(entry)
            item["type"] = "hard"
            result.append(item)
        for entry in self._soft_blacklist.values():
            # 已过期的软拉黑不展示（check() 里会惰性清理，这里只做过滤不改状态）
            if entry.expires_at > 0 and now > entry.expires_at:
                continue
            item = asdict(entry)
            item["type"] = "soft"
            result.append(item)
        result.sort(key=lambda x: float(x.get("added_time") or 0.0), reverse=True)
        return result

    def remove_from_blacklist(self, user_id: str) -> bool:
        """解除拉黑。"""
        removed = False
        if user_id in self._hard_blacklist:
            del self._hard_blacklist[user_id]
            removed = True
        if user_id in self._soft_blacklist:
            del self._soft_blacklist[user_id]
            removed = True
        if removed:
            self._save()
        return removed

    def _check_affinity_blacklist(self, user_id: str) -> str:
        """根据好感度自动拉黑：厌恶(-50)→软拉黑，仇恨(-100)→硬拉黑。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            points = stats.get("points", 0)

            if points <= -100 and user_id not in self._hard_blacklist:
                # 仇恨级别→永久拉黑
                self.add_to_blacklist(
                    user_id, reason="好感度降至仇恨级别", permanent=True
                )
                return FilterResult.BLOCK
            elif points <= -50 and user_id not in self._soft_blacklist:
                # 厌恶级别→软拉黑
                self.add_to_blacklist(
                    user_id, reason="好感度降至厌恶级别", permanent=False,
                    expire_hours=48,
                )
                return FilterResult.BLOCK
        except Exception:
            pass
        return FilterResult.ALLOW

    def add_to_whitelist(self, user_id: str) -> None:
        """加入白名单。"""
        self._whitelist.add(user_id)
        self._save()

    def verify_user(self, user_id: str) -> None:
        """标记用户为已验证（跳过后续检测）。"""
        self._verified.add(user_id)
        self._save()

    def set_mode(self, mode: str) -> None:
        """设置过滤模式。"""
        if mode in (FilterMode.OFF, FilterMode.WHITELIST, FilterMode.BLACKLIST):
            self._mode = mode
            self._save()

    @property
    def stats(self) -> dict:
        return {
            "mode": self._mode,
            "whitelist_count": len(self._whitelist),
            "hard_blacklist": len(self._hard_blacklist),
            "soft_blacklist": len(self._soft_blacklist),
            "verified": len(self._verified),
        }

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self) -> None:
        try:
            data = {
                "mode": self._mode,
                "whitelist": list(self._whitelist),
                "hard_blacklist": {k: asdict(v) for k, v in self._hard_blacklist.items()},
                "soft_blacklist": {k: asdict(v) for k, v in self._soft_blacklist.items()},
                "verified": list(self._verified),
            }
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            self._mode = data.get("mode", FilterMode.BLACKLIST)
            self._whitelist = set(data.get("whitelist", []))
            self._verified = set(data.get("verified", []))
            for k, v in data.get("hard_blacklist", {}).items():
                self._hard_blacklist[k] = BlacklistEntry(**v)
            for k, v in data.get("soft_blacklist", {}).items():
                self._soft_blacklist[k] = BlacklistEntry(**v)
        except Exception:
            pass


# ================================================================
# 自动发现接口
# ================================================================

class UserFilterModule(MemoryModule):
    """用户过滤器模块 — 自动发现注册。"""
    name = "user_filter"

    def init(self, data_dir="data/memory", **kwargs):
        owner_id = ""
        try:
            import yaml
            from pathlib import Path
            conf_path = Path("conf.yaml")
            if conf_path.exists():
                conf = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
                family = conf.get("qq", {}).get("family_qq", [])
                if family:
                    owner_id = str(family[0])
        except Exception:
            pass
        self._impl = UserFilter(data_dir=data_dir, owner_id=owner_id)


MODULE = UserFilterModule
