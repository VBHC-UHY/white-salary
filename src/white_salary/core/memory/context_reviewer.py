"""
white_salary/core/memory/context_reviewer.py

上下文审查器 — 每N轮对话自动用LLM提取值得长期保存的信息。

借鉴v2的context_reviewer.py：
  - 每20轮对话触发一次审查（可配置）
  - 用memory_llm分析对话内容，提取重要信息
  - 提取结果存入长期记忆
  - 最小间隔300秒防频繁触发
  - 后台异步执行，不阻塞对话

配置从 config/memory_settings.json 的 context_reviewer 节读取。

自动发现：导出MODULE供MemoryManager加载。
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# LLM提取提示词
# ================================================================

_EXTRACT_SYSTEM = "你是记忆分析助手，专门从对话中提取值得长期记住的信息。只输出提取结果。"

_EXTRACT_PROMPT = """你是记忆分析助手。请仔细阅读以下对话，提取值得**长期记住**的重要信息。

【对话内容】
{conversation}

【提取规则】
只提取以下类型的信息：
1. 用户的个人信息（名字、年龄、职业、住址、生日等）
2. 用户的偏好和习惯（喜欢什么、讨厌什么、习惯做什么）
3. 重要事件（旅行计划、考试、工作变动、感情状态等）
4. 承诺和约定（答应了什么、约了什么时候）
5. 情感时刻（特别开心/难过/生气的原因）
6. 人际关系（提到了谁、跟谁什么关系）

【输出格式】
如果有值得记住的，每条用一行，格式：
- [类型] 具体内容

如果这段对话没有任何值得长期记住的信息，只回答：
无

【注意】
- 不要记录日常闲聊（"哈哈""嗯""好的"）
- 不要记录AI说的话（只记用户的信息）
- 每条要具体、简洁、有信息量
- 最多提取{max_extract}条"""


# ================================================================
# 数据结构
# ================================================================

@dataclass
class ReviewState:
    """会话审查状态。"""
    count: int = 0                          # 消息计数
    last_review_time: float = 0.0           # 上次审查时间
    recent_messages: list[dict] = field(default_factory=list)  # 最近的消息


class ContextReviewer:
    """
    上下文审查器。

    使用方式:
        reviewer = ContextReviewer(config)
        # 每次对话后调用
        reviewer.on_message(session_id, user_msg, ai_reply)
        # 内部自动触发审查
    """

    def __init__(self, config: dict = None, data_dir: str = "data/memory") -> None:
        cfg = config or {}
        self._window_size = cfg.get("review_interval_turns", 20)
        self._max_extract = cfg.get("max_extract_per_review", 5)
        self._min_interval = cfg.get("min_interval", 300)  # 秒
        self._enabled = cfg.get("enabled", True)

        self._data_dir = data_dir

        # 每个会话独立的审查状态
        self._sessions: dict[str, ReviewState] = {}
        # 正在审查中的会话（防重复）
        self._reviewing: set[str] = set()

    def on_message(self, session_id: str, user_msg: str,
                   ai_reply: str = "") -> None:
        """
        每次对话后调用。累积消息，达到阈值触发审查。

        同步方法，审查异步后台执行。
        """
        if not self._enabled or not user_msg:
            return

        # 获取或创建会话状态
        if session_id not in self._sessions:
            self._sessions[session_id] = ReviewState()
        state = self._sessions[session_id]

        # 记录消息
        now_str = datetime.now().strftime("%H:%M")
        state.recent_messages.append({
            "role": "user",
            "content": user_msg[:500],
            "time": now_str,
        })
        if ai_reply:
            state.recent_messages.append({
                "role": "assistant",
                "content": ai_reply[:500],
                "time": now_str,
            })

        # 限制缓冲区大小
        max_buf = self._window_size * 2 + 10
        if len(state.recent_messages) > max_buf:
            state.recent_messages = state.recent_messages[-max_buf:]

        state.count += 1

        # 检查是否触发审查
        if state.count >= self._window_size:
            # 检查最小间隔
            if time.time() - state.last_review_time < self._min_interval:
                return
            # 检查是否已在审查中
            if session_id in self._reviewing:
                return

            # 触发审查
            state.count = 0
            state.last_review_time = time.time()
            messages = list(state.recent_messages)

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._do_review(session_id, messages))
                else:
                    loop.run_until_complete(self._do_review(session_id, messages))
            except RuntimeError:
                # 没有event loop
                pass

    async def _do_review(self, session_id: str, messages: list[dict]) -> None:
        """异步执行审查。"""
        self._reviewing.add(session_id)
        try:
            # 构建对话文本
            conversation = self._format_conversation(messages)
            if not conversation:
                return

            # 调用LLM提取
            extracted = await self._extract_with_llm(conversation)
            if not extracted:
                logger.debug(f"[ContextReviewer] {session_id}: 无值得保存的信息")
                return

            # 存入长期记忆
            saved_count = await self._save_to_memory(extracted, session_id)
            logger.info(
                f"[ContextReviewer] {session_id}: 提取并保存了{saved_count}条信息"
            )

        except Exception as e:
            logger.error(f"[ContextReviewer] 审查失败: {e}")
        finally:
            self._reviewing.discard(session_id)

    def _format_conversation(self, messages: list[dict]) -> str:
        """格式化对话文本。"""
        lines = []
        for msg in messages:
            time_str = msg.get("time", "")
            role = "用户" if msg["role"] == "user" else "白"
            content = msg["content"]
            lines.append(f"[{time_str}] {role}: {content}")
        return "\n".join(lines)

    async def _extract_with_llm(self, conversation: str) -> list[str]:
        """用LLM提取重要信息。"""
        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            from white_salary.core.llm.llm_manager import LLMManager

            llm = LLMManager()
            prompt_text = _EXTRACT_PROMPT.format(
                conversation=conversation,
                max_extract=self._max_extract,
            )
            messages = [
                Message(role=MessageRole.SYSTEM, content=_EXTRACT_SYSTEM),
                Message(role=MessageRole.USER, content=prompt_text),
            ]

            reply = await llm.chat(
                messages=messages,
                channel="memory",
                temperature=0.3,
                max_tokens=500,
            )

            if not reply:
                return []

            reply = reply.strip()
            if reply == "无" or reply == "无。":
                return []

            # 解析每一行
            extracted = []
            for line in reply.split("\n"):
                line = line.strip()
                if line.startswith("- ") or line.startswith("· "):
                    line = line[2:].strip()
                if line and len(line) > 3 and line != "无":
                    extracted.append(line)

            return extracted[:self._max_extract]

        except ImportError:
            logger.debug("[ContextReviewer] LLM模块不可用")
            return []
        except Exception as e:
            logger.error(f"[ContextReviewer] LLM提取失败: {e}")
            return []

    async def _save_to_memory(self, extracted: list[str], session_id: str) -> int:
        """将提取的信息存入长期记忆。"""
        saved = 0
        try:
            from white_salary.core.memory.manager import MemoryManager

            # 2026-07-03 审计修复（批5）：MemoryManager() 现返回进程级共享实例
            # （按data_dir缓存），不再每次复盘都重建全套store+扩展模块
            manager = MemoryManager()
            date_str = datetime.now().strftime("%Y-%m-%d")

            for info in extracted:
                # 格式化为带日期的记忆
                memory_text = f"[{date_str}] {info}"
                source = f"context_review:{session_id}"

                try:
                    await manager.add_long_term(
                        content=memory_text,
                        source=source,
                        layer="fact",
                        importance=6,
                    )
                    saved += 1
                except AttributeError:
                    # manager可能没有add_long_term方法，尝试其他方式
                    try:
                        manager.add_memory(memory_text, source=source)
                        saved += 1
                    except Exception:
                        pass

        except ImportError:
            logger.debug("[ContextReviewer] MemoryManager不可用")
        except Exception as e:
            logger.error(f"[ContextReviewer] 保存失败: {e}")

        return saved

    @property
    def stats(self) -> dict:
        """统计信息。"""
        return {
            "enabled": self._enabled,
            "window_size": self._window_size,
            "active_sessions": len(self._sessions),
            "reviewing": len(self._reviewing),
            "session_counts": {
                sid: state.count for sid, state in self._sessions.items()
            },
        }


# ================================================================
# 自动发现接口
# ================================================================

class ContextReviewerModule(MemoryModule):
    """上下文审查器模块 — 自动发现注册。"""
    name = "context_reviewer"

    def init(self, data_dir="data/memory", **kwargs):
        config = {}
        try:
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                all_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                config = all_cfg.get("context_reviewer", {})
        except Exception:
            pass
        self._impl = ContextReviewer(config=config, data_dir=data_dir)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """每次对话后调用，累积消息并触发审查。"""
        if not hasattr(self, '_impl') or not user_msg:
            return
        # 使用默认会话ID（单用户桌面应用）
        self._impl.on_message("default", user_msg, ai_reply)


MODULE = ContextReviewerModule
