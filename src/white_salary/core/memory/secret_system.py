"""
white_salary/core/memory/secret_system.py

秘密系统 — 保密特定信息，防止泄露给不该知道的人。

借鉴v2的features/secret_system.py（196行）：
  - 用户说"这是秘密"→标记为秘密
  - 秘密有来源（谁告诉的）和保密对象（不能告诉谁）
  - 对话时检查是否快要泄露秘密，注入警告
  - 不用LLM，纯关键词检测

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


class SecretLevel:
    """秘密等级。"""
    OPEN = 1         # 公开 — 可以随便说
    PRIVATE = 2      # 私密 — 只告诉好感≥5的人
    SECRET = 3       # 秘密 — 只告诉好感≥7的人（家人/密友）
    DEEP_SECRET = 4  # 深层秘密 — 永远不主动说


@dataclass
class Secret:
    """一条秘密。"""
    secret_id: str = ""
    content: str = ""               # 秘密内容
    told_by: str = ""               # 谁告诉的
    told_by_name: str = ""
    level: int = 3                  # 秘密等级（默认SECRET）
    shared_with: list[str] = field(default_factory=list)  # 已经知道的人
    not_tell: list[str] = field(default_factory=list)  # 不能告诉谁
    importance: int = 8             # 秘密重要度
    created_at: float = 0.0


# 秘密检测关键词
_SECRET_MARKERS = [
    "秘密", "只告诉你", "别说出去", "别跟别人说", "不要告诉",
    "保密", "偷偷", "悄悄", "私下", "我们的秘密",
    "不要让别人知道", "别让人知道", "别说出去",
]

# 泄露风险检测（当对话中提到秘密相关内容时警告）
_LEAK_RISK_KEYWORDS = ["告诉", "说给", "跟他说", "跟她说", "分享"]

MAX_SECRETS = 100


class SecretStore:
    """
    秘密存储。

    使用方式:
        store = SecretStore(data_dir)
        store.detect_and_store("这是我们的秘密，我喜欢小红", user_id="user1")
        warning = store.check_leak_risk("我想告诉小明一件事")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "secrets.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets: list[Secret] = []
        self._load()

    def detect_and_store(self, message: str, user_id: str = "",
                         user_name: str = "") -> Optional[Secret]:
        """检测消息中是否包含秘密，自动存储。"""
        if not message:
            return None

        is_secret = False
        for marker in _SECRET_MARKERS:
            if marker in message:
                is_secret = True
                break

        if not is_secret:
            return None

        # 提取秘密内容（去掉标记词后的内容）
        content = message
        for marker in _SECRET_MARKERS:
            content = content.replace(marker, "")
        content = content.strip("，。！？、 ")

        if len(content) < 3:
            content = message  # 内容太短就保留原文

        secret = Secret(
            secret_id=f"secret_{int(time.time() * 1000)}",
            content=content[:200],
            told_by=user_id,
            told_by_name=user_name,
            importance=8,
            created_at=time.time(),
        )
        self._secrets.append(secret)

        if len(self._secrets) > MAX_SECRETS:
            self._secrets = self._secrets[-MAX_SECRETS:]

        self._save()
        logger.debug(f"[Secret] 记录秘密: {content[:30]}...")
        return secret

    def check_leak_risk(self, message: str) -> Optional[str]:
        """
        检查当前消息是否有泄露秘密的风险。

        Returns:
            警告文本（None=无风险）
        """
        if not self._secrets or not message:
            return None

        # 检查是否在讨论"告诉别人"的话题
        has_leak_intent = any(kw in message for kw in _LEAK_RISK_KEYWORDS)

        # 检查消息中是否包含秘密的关键词
        for secret in self._secrets:
            # 提取秘密中的关键词（简单分词）
            secret_words = [w for w in secret.content if len(w) >= 2]
            secret_text = secret.content

            # 如果消息中提到了秘密相关的内容
            overlap = sum(1 for w in secret_text.split() if w in message)
            if overlap > 0 or any(w in message for w in secret_text[:10].split()):
                if has_leak_intent:
                    return (
                        f"[秘密警告] 小心！这可能涉及"
                        f"{secret.told_by_name or '用户'}告诉你的秘密。"
                        f"不要泄露给别人。"
                    )

        return None

    def get_secrets_prompt(self) -> str:
        """生成秘密提醒（注入system prompt）。"""
        if not self._secrets:
            return ""

        recent = self._secrets[-5:]
        lines = ["[你保守的秘密（绝对不能泄露给别人）]"]
        for s in recent:
            who = s.told_by_name or "用户"
            lines.append(f"  - {who}的秘密: {s.content[:40]}")
        return "\n".join(lines)

    def can_share(self, secret: Secret, user_id: str,
                  affinity_level: int = 0) -> bool:
        """
        判断能否把秘密告诉某人。

        Args:
            secret: 秘密
            user_id: 要告诉的人
            affinity_level: 与这个人的好感等级(0-10)
        """
        # 已经知道的人
        if user_id in secret.shared_with:
            return True
        # 不能告诉的人
        if user_id in secret.not_tell:
            return False
        # 按等级判断
        if secret.level == SecretLevel.OPEN:
            return True
        elif secret.level == SecretLevel.PRIVATE:
            return affinity_level >= 5
        elif secret.level == SecretLevel.SECRET:
            return affinity_level >= 7
        elif secret.level == SecretLevel.DEEP_SECRET:
            return False
        return False

    def share_secret(self, secret_id: str, user_id: str) -> None:
        """标记某人已知某秘密。"""
        for s in self._secrets:
            if s.secret_id == secret_id:
                if user_id not in s.shared_with:
                    s.shared_with.append(user_id)
                    self._save()
                return

    def get_all_secrets(self) -> list[Secret]:
        return list(self._secrets)

    def remove_secret(self, secret_id: str) -> bool:
        before = len(self._secrets)
        self._secrets = [s for s in self._secrets if s.secret_id != secret_id]
        if len(self._secrets) < before:
            self._save()
            return True
        return False

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self) -> None:
        try:
            data = [asdict(s) for s in self._secrets]
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Secret] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for d in data:
                self._secrets.append(Secret(**d))
            logger.debug(f"[Secret] 加载: {len(self._secrets)}条秘密")
        except Exception as e:
            logger.warning(f"[Secret] 加载失败: {e}")

    @property
    def stats(self) -> dict:
        return {"total_secrets": len(self._secrets)}


# ================================================================
# 自动发现接口
# ================================================================

class SecretSystemModule(MemoryModule):
    name = "secret_system"

    def init(self, data_dir: str = "data/memory", **kwargs) -> None:
        self._impl = SecretStore(data_dir=data_dir)

    def get_context_prompt(self, message: str = "",
                           user_id: str = "desktop",
                           is_group: bool = False) -> str:
        """
        2026-07-02 审计修复（批4）：秘密注入隔离。

        旧实现（旧签名，不收user_id）把"你保守的秘密"无差别注入每个用户
        （含QQ陌生人）的system prompt，可被套话泄露。改为新签名接收user_id
        （参照emotion_memory.py:278的已修写法），只有主人才注入秘密提醒，
        其他用户一律返回空串。
        """
        if not hasattr(self, '_impl'):
            return ""
        # 只有主人才注入秘密内容；判定失败一律按非主人处理（隐私优先）
        try:
            from white_salary.core.memory.manager import is_owner_user
            if not is_owner_user(user_id):
                return ""
        except Exception as e:
            logger.warning(f"[Secret] 主人身份判定失败，按非主人处理不注入秘密: {e}")
            return ""
        # 检查泄露风险
        warning = self._impl.check_leak_risk(message) if message else None
        # 注入秘密提醒（仅主人上下文）
        prompt = self._impl.get_secrets_prompt()
        if warning:
            prompt = warning + "\n" + prompt
        return prompt

    def on_message(self, user_msg: str = "", ai_reply: str = "",
                   user_id: str = "desktop",
                   is_group: bool = False) -> None:
        # 2026-07-02 审计修复（批4）：改新签名，秘密来源记录真实user_id（told_by）
        if user_msg and hasattr(self, '_impl'):
            self._impl.detect_and_store(user_msg, user_id=user_id)


MODULE = SecretSystemModule
