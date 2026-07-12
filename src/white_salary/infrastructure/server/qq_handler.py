"""
white_salary/infrastructure/server/qq_handler.py

QQ消息处理器 — 连接QQ适配器和ChatAgent。

功能：
  - 接收QQ消息 → 调用ChatAgent生成回复 → 发送回QQ
  - 支持私聊和群聊
  - 记忆和好感度按QQ用户独立管理（后续扩展）
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.agent.session_pool import (
    ChatAgentSessionPool,
    qq_conversation_key,
    qq_session_key,
)
from white_salary.core.affinity.manager import AffinityManager
from white_salary.core.interfaces.types import AudioData, Message, MessageRole
from white_salary.core.runtime import (
    ChannelAddress,
    ConversationRef,
    InteractiveTaskHandle,
    InteractiveTaskJournal,
    RuntimeStore,
)
from white_salary.adapters.platform.qq_adapter import QQAdapter, QQMessage
# 2026-07-03 面板升级（批6）：功能开关配置模型（features 节，纯Pydantic无循环依赖）
from white_salary.infrastructure.config.models import FeaturesConfig


def _get_plugin_manager():
    """
    2026-07-03 功能大项（批11）：从 settings_api 运行实例注册表取 PluginManager。

    run_server 创建 PluginManager 后经 register_runtime_instance('plugins', ...)
    登记；这里取用于消息钩子（on_message 抢答 / on_reply 改写）。
    取不到（未注册/注册表异常）返回 None，调用方跳过钩子、按原流程走，
    绝不因插件系统缺失而报错。

    Returns:
        PluginManager 实例或 None
    """
    try:
        from white_salary.infrastructure.server.settings_api import get_runtime_instance
        return get_runtime_instance("plugins")
    except Exception as e:
        logger.debug(f"[QQ] 取插件管理器失败（跳过插件钩子）: {e}")
        return None


def _resolve_features(features: Optional[FeaturesConfig] = None) -> FeaturesConfig:
    """
    2026-07-03 面板升级（批6）：解析功能开关配置。

    run_server 装配时显式传入 config.features；未传（旧调用方/测试）时
    自行走 load_config() 读合并配置，读取失败保守回退全默认值
    （全 True = 原硬编码行为）。

    Args:
        features: 显式传入的功能开关配置；None=自行读取

    Returns:
        FeaturesConfig 功能开关配置
    """
    if features is not None:
        return features
    try:
        from white_salary.infrastructure.config import load_config
        return load_config().features
    except Exception as e:
        logger.warning(f"[QQ] features 配置读取失败，回退全默认开关(全开): {e}")
        return FeaturesConfig()


def _expected_memory_tag(user_name: str, group_id: str, is_group: bool) -> str:
    """
    2026-07-02 审计修复（批4）：复刻 ChatAgent._tag_response 的来源标记前缀规则。

    撤销重生成写入的记忆时，用这个前缀锁定"本轮生成的AI回复"，
    防止并发场景误删其他用户对话的回复。

    Args:
        user_name: 本轮消息的发送者名字
        group_id: 群号（私聊传空串）
        is_group: 是否群聊

    Returns:
        AI回复存入短期记忆时的前缀（可能为空串=无标记）
    """
    name = user_name or "用户"
    if is_group and group_id:
        return f"[回复 群{group_id} {name}] "
    if name != "用户":
        return f"[回复 {name}] "
    return ""


async def _transcribe_qq_voice(asr_adapter, audio_bytes: bytes, dtype: str = "mp3") -> str:
    """用统一 ASRInterface.transcribe 识别 QQ 语音字节。"""
    if not asr_adapter or not audio_bytes:
        return ""

    if hasattr(asr_adapter, "transcribe"):
        result = await asr_adapter.transcribe(
            AudioData(samples=audio_bytes, sample_rate=16000, dtype=dtype)
        )
        return str(getattr(result, "text", "") or "").strip()

    # 兼容极老的自定义适配器；正式接口仍以 transcribe 为准。
    if hasattr(asr_adapter, "recognize"):
        result = await asr_adapter.recognize(audio_bytes)
        return str(result or "").strip()

    return ""


def _undo_generation_pair(
    messages: list,
    before_ids: set[int],
    user_input: str,
    expected_tag: str,
) -> int:
    """
    2026-07-02 审计修复（批4）：按对象身份撤销本轮生成写入的一问一答。

    原代码直接 pop 共享 ShortTermMemory 的末两条——所有QQ用户共用一个
    agent 记忆，用户A重生成时若用户B的问答在A之后写入，pop 删掉的是B的记录。
    现改为：chat 前快照消息对象引用（before_ids），撤销时只删"本轮新增
    （id不在快照里）且内容/来源标记匹配本轮"的两条，按对象身份（is）删除，
    找不到就不删（宁可残留也不误删他人对话）。

    Args:
        messages: agent._memory._messages（原地修改）
        before_ids: 本轮 chat 前已存在消息的 id() 集合
                    （调用方必须持有快照列表的强引用，防止 id 复用）
        user_input: 本轮发给 agent 的用户输入原文
        expected_tag: 本轮AI回复在记忆里的来源标记前缀（空串=无标记）

    Returns:
        实际删除的消息条数（0~2）
    """
    from white_salary.core.interfaces.types import MessageRole

    # 1. 找本轮的用户消息：新增 + USER + 内容与本轮输入完全一致
    user_idx = -1
    user_obj = None
    for i, m in enumerate(messages):
        if id(m) in before_ids:
            continue
        if m.role == MessageRole.USER and m.content == user_input:
            user_idx = i
            user_obj = m
            break

    # 2. 找本轮的AI回复：用户消息之后新增的ASSISTANT
    assistant_obj = None
    if user_idx >= 0:
        candidates = [
            m for i, m in enumerate(messages)
            if i > user_idx and id(m) not in before_ids
            and m.role == MessageRole.ASSISTANT
        ]
        if expected_tag:
            # 有来源标记时必须前缀匹配，防止删到并发轮次的回复
            for m in candidates:
                if m.content.startswith(expected_tag):
                    assistant_obj = m
                    break
        else:
            # 无标记（罕见：发送者名字为空的私聊）：取第一条同样无标记的新增回复
            for m in candidates:
                if not m.content.startswith("[回复"):
                    assistant_obj = m
                    break

    # 3. 按对象身份删除（Message是frozen dataclass带__eq__，
    #    list.remove会按值匹配到别的等值消息，必须用 is 逐个比对）
    removed = 0
    for target in (assistant_obj, user_obj):
        if target is None:
            continue
        for i, m in enumerate(messages):
            if m is target:
                del messages[i]
                removed += 1
                break
    return removed


class _RedeliveredMsg:
    """
    2026-07-02 审计修复（批4）：重生成达上限后排队消息的重投递载体。

    只携带 handle_qq_message / adapter.send_reply 用到的字段；
    带 _is_redelivered 标记——重投递的消息不再进缓冲/拦截合并，
    直接普通处理一轮（防止重投递再触发拦截造成死循环）。
    """

    def __init__(self, original: QQMessage, merged_text: str) -> None:
        """
        Args:
            original: 触发本轮生成的原始QQ消息（复制目标/身份字段）
            merged_text: 被丢弃的排队消息合并后的文本
        """
        self.text: str = merged_text
        self.user_id: str = original.user_id
        self.group_id: str = original.group_id if original.is_group else ""
        self.is_group: bool = original.is_group
        self.sender_name: str = original.sender_name
        self.is_at_me: bool = bool(getattr(original, "is_at_me", False))
        self.self_id: str = original.self_id
        self.sub_type: str = ""
        self.image_urls: list[str] = []
        self.message_id: int = 0
        self._is_redelivered: bool = True


def _compose_decision_raw_part(raw_message: str, text: str, original_text: str) -> str:
    """
    Build the raw text used by SmartReply after ASR/Vision enrichment.

    SmartReply still needs the original CQ codes for @/reply/media checks, but
    semantic continuation also needs the enriched text, such as image captions.
    """
    raw = raw_message or original_text or text or ""
    enriched = text or ""
    if enriched and enriched != original_text and enriched not in raw:
        return f"{raw}\n{enriched}" if raw else enriched
    return raw or enriched


def _is_stop_reply_request(text: str) -> bool:
    """Return whether *text* explicitly asks Bai to stop this conversation."""

    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return False
    stop_patterns = (
        "别回", "不要回", "不用回", "别回复", "不要回复", "不用回复", "无需回复",
        "别说话", "不要说话", "先别说", "暂时别说", "闭嘴",
        "不许发消息", "不要发消息", "别发消息", "不准发消息",
        "别回我", "别回话", "别理我", "不用理我",
    )
    return any(pattern in normalized for pattern in stop_patterns)


def _parse_continuation_reply(content: str, *, fallback: bool) -> bool:
    """Parse the continuation gate without treating negative phrases as positive."""

    text = (content or "").strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            value = json.loads(match.group(0)).get("reply")
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"false", "no", "0"}:
                    return False
                if normalized in {"true", "yes", "1"}:
                    return True
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            pass

    lowered = text.lower()
    negative_markers = (
        "false", "不应该回复", "不需要回复", "不要回复", "无需回复",
        "不用回复", "不回复", "不该回复",
    )
    if any(marker in lowered for marker in negative_markers):
        return False

    positive_markers = ("true", "应该回复", "需要回复")
    if any(marker in lowered for marker in positive_markers):
        return True
    return fallback


class QQContextManager:
    """
    QQ群聊上下文管理 — 每个群/用户独立的对话历史（带持久化）。
    """
    def __init__(self, max_messages: int = 20, data_dir: str = "data/qq"):
        self._contexts: dict[str, list[dict]] = {}
        self._max = max_messages
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    @staticmethod
    def group_key(group_id: str) -> str:
        return f"group:{str(group_id or '').strip()}"

    @staticmethod
    def private_key(user_id: str) -> str:
        return f"private:{str(user_id or '').strip()}"
    def add_message(self, context_id: str, sender: str, text: str) -> None:
        if context_id not in self._contexts:
            self._contexts[context_id] = []
        stored_text = (text or "")[:300]
        if not stored_text:
            return
        last = self._contexts[context_id][-1] if self._contexts[context_id] else None
        if last and last.get("sender") == sender and last.get("text") == stored_text:
            return
        self._contexts[context_id].append({"sender": sender, "text": stored_text})
        if len(self._contexts[context_id]) > self._max:
            self._contexts[context_id] = self._contexts[context_id][-self._max:]
        self._save()

    def get_context(self, context_id: str) -> str:
        msgs = self._contexts.get(context_id, [])
        if not msgs:
            return ""
        title = "[最近的群聊记录]" if context_id.startswith("group:") else "[最近的私聊记录]"
        lines = [title]
        for m in msgs[-10:]:
            lines.append(f"  {m['sender']}: {m['text']}")
        return "\n".join(lines)

    def _save(self) -> None:
        try:
            with open(self._data_dir / "contexts.json", "w", encoding="utf-8") as f:
                json.dump(self._contexts, f, ensure_ascii=False)
        except Exception as _e:
                logger.debug(f'[QQ] 静默异常: {_e}')

    def _load(self) -> None:
        path = self._data_dir / "contexts.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    return
                # Old releases used one bare numeric key space for both groups
                # and private users. Preserve those records under an explicit
                # legacy namespace, but never inject them automatically because
                # a QQ number can equal a group number.
                self._contexts = {
                    (
                        str(key)
                        if str(key).startswith(("group:", "private:", "legacy:"))
                        else f"legacy:{key}"
                    ): value
                    for key, value in loaded.items()
                    if isinstance(value, list)
                }
            except Exception as _e:
                    logger.debug(f'[QQ] 静默异常: {_e}')


def _should_consume_stop_request(
    *,
    msg: QQMessage,
    text: str,
    is_direct: bool,
    engagement_leases,
) -> bool:
    """Scope a stop request to the addressed or currently engaged QQ user."""

    if not _is_stop_reply_request(text):
        return False
    if not msg.is_group or is_direct:
        return True
    try:
        return engagement_leases.is_candidate(
            QQContextManager.group_key(msg.group_id),
            msg.user_id,
        )
    except Exception as exc:
        logger.warning(f"[QQ] 检查停止回复活动窗口失败，按未命中处理: {exc}")
        return False


def _is_user_blocked(user_filter, user_id: str, *, is_family: bool) -> bool:
    """Use the persistent runtime filter for every QQ message/event path."""

    if user_filter is None or is_family:
        return False
    try:
        from white_salary.core.memory.user_filter import FilterResult

        return user_filter.check(user_id) == FilterResult.BLOCK
    except Exception as exc:
        logger.warning(f"[QQ] 用户过滤检查失败，按放行处理: {exc}")
        return False


def _record_silent_tool_completion(
    *,
    ctx_manager: QQContextManager,
    context_id: str,
    bot_name: str,
    msg: QQMessage,
    record_reply: Callable[[QQMessage], None],
) -> None:
    """Record a side-effect-only tool action without sending extra QQ text."""
    ctx_manager.add_message(
        context_id,
        bot_name,
        "[白通过工具完成了这次请求，没有额外发送文字]",
    )
    record_reply(msg)


async def start_qq_service(
    agent: ChatAgent,
    ws_url: str = "ws://127.0.0.1:3001",
    bot_name: str = "白",
    token: str = "",
    family_qq: Optional[list[str]] = None,
    user_learning=None,
    asr_adapter=None,
    vision_adapter=None,
    features: Optional[FeaturesConfig] = None,
    wake_words: Optional[list[str]] = None,
    unblocked_group_ids: Optional[list[str]] = None,
    continuation_llm=None,
    project_root: Optional[Path] = None,
    agent_sessions: Optional[ChatAgentSessionPool] = None,
    runtime_store: Optional[RuntimeStore] = None,
) -> None:
    """
    启动QQ服务（在后台运行）。

    Args:
        agent: ChatAgent实例
        ws_url: NapCat WebSocket地址
        bot_name: 机器人名字
        token: NapCat鉴权token
        family_qq: 家人QQ号列表（自动设为家人关系）
        asr_adapter: 语音识别适配器（收到语音消息时用）
        vision_adapter: 视觉适配器（收到图片消息时用）
        wake_words: QQ端唤醒词列表；只影响QQ群聊，不影响桌面端。
        unblocked_group_ids: 手动不屏蔽的QQ群号列表；提高语义续聊候选分，不影响桌面端。
        continuation_llm: QQ活跃窗口续聊判断模型；None=用保守分数兜底。
        project_root: 项目根目录，用于群内指令持久化 QQ 配置。
        agent_sessions: QQ会话短期记忆池；None时按项目目录自动创建。
        features: 2026-07-03 面板升级（批6）：功能开关配置（run_server 传
                  config.features；None=自行读合并配置，失败回退全开=原行为）。
                  topic_tracker/rest_system 开关在此消费
    """
    # 2026-07-03 面板升级（批6）：解析功能开关（默认全开=现状）
    feats: FeaturesConfig = _resolve_features(features)
    # 2026-07-02 审计修复（批4）：把 family_qq 传给适配器（入群邀请白名单判定用）
    adapter = QQAdapter(
        ws_url=ws_url, bot_name=bot_name, token=token,
        family_qq=[str(q) for q in (family_qq or [])],
    )
    _service_root = Path(project_root) if project_root is not None else Path.cwd()
    if runtime_store is None:
        runtime_store = RuntimeStore(
            _service_root / "data" / "runtime" / "agent_runtime.db"
        )
    runtime_journal = InteractiveTaskJournal(runtime_store)
    ctx_manager = QQContextManager()
    if agent_sessions is None:
        agent_sessions = ChatAgentSessionPool(
            agent,
            _service_root / "data" / "chat_history" / "qq_sessions",
            max_turns=int(getattr(getattr(agent, "_memory", None), "_max_turns", 20)),
        )
    try:
        from white_salary.infrastructure.server.settings_api import (
            register_runtime_instance as _register_rt,
        )
        _register_rt("qq_agent_sessions", agent_sessions)
    except Exception as e:
        logger.warning(f"[QQ] 登记QQ会话记忆池失败（设置页无法即时清空）: {e}")

    def _session_agent_for(msg: QQMessage) -> ChatAgent:
        return agent_sessions.get(
            qq_session_key(
                user_id=msg.user_id,
                group_id=msg.group_id if msg.is_group else "",
                is_group=msg.is_group,
            )
        )

    # 把QQ适配器注册到QQ API工具（让桌面端也能调QQ API）
    try:
        from white_salary.adapters.tools.builtin.qq_api import set_qq_adapter
        set_qq_adapter(adapter)
        logger.info("[QQ] QQ适配器已注册到工具系统")
    except Exception as e:
        logger.warning(f"[QQ] 注册QQ适配器失败: {e}")

    # 2026-07-03 工具实现（批9）：把运行中的QQ适配器与其事件循环登记到
    # settings_api 运行实例注册表——run_server 注入 ReminderService 的QQ发送
    # 回调经此取用（run_coroutine_threadsafe 跨线程调度到本循环），
    # 提醒服务因此不直接依赖qq模块
    try:
        from white_salary.infrastructure.server.settings_api import (
            register_runtime_instance as _register_rt,
        )
        _register_rt("qq_adapter", adapter)
        _register_rt("qq_loop", asyncio.get_running_loop())
    except Exception as e:
        logger.warning(f"[QQ] 登记QQ适配器/事件循环失败（提醒的QQ通道不可用）: {e}")

    # 群消息上下文记录（所有群消息都记，不管白回不回复，@白时能看到之前聊了什么）
    def _record_group_msg(group_id: str, sender_name: str, text: str) -> None:
        ctx_manager.add_message(
            QQContextManager.group_key(group_id),
            sender_name,
            text,
        )

    adapter.on_group_record = _record_group_msg

    # QQ端功能模块初始化
    from white_salary.core.topic_tracker import TopicTracker
    from white_salary.core.conflict_detector import ConflictDetector, ConflictType
    from white_salary.core.llm_enhancer import LLMEnhancer
    from white_salary.core.social.manager import SocialManager
    from white_salary.core.message.processing import TimePerception, MessageBuffer
    from white_salary.core.rest_system import RestSystem

    # 2026-07-03 面板升级（批6）：话题追踪/休息系统按 features 开关创建——
    # 关闭时置 None、调用点判空跳过，修复两个面板开关零消费方的问题
    # （依据 docs/panel-audit-2026-07-03/panel-chatcfg.json；默认开=原行为）
    qq_topic_tracker: Optional[TopicTracker] = (
        TopicTracker() if feats.topic_tracker else None
    )
    qq_conflict_detector = ConflictDetector()
    qq_enhancer = LLMEnhancer()
    qq_social = SocialManager(owner_ids=[str(q) for q in (family_qq or [])])
    qq_time = TimePerception()
    qq_rest: Optional[RestSystem] = (
        RestSystem(data_dir="data") if feats.rest_system else None
    )
    # 2026-07-03 工具实现（批9）：忙碌/静默状态（进程级单例，与工具层/桌面端共用）——
    # set_quiet_mode 工具写入的状态在这里被消费，白在QQ端真的会闭嘴
    from white_salary.core.services.presence_state import PresenceState
    qq_presence = PresenceState.get_instance()
    if qq_topic_tracker is None:
        logger.info("[QQ] 话题追踪已按 features.topic_tracker=false 关闭")
    if qq_rest is None:
        logger.info("[QQ] 休息系统已按 features.rest_system=false 关闭")

    # 2026-07-03 面板升级（批6）：用户过滤器提前到服务启动时创建并注册到
    # settings_api 运行实例注册表（键 'user_filter'）——设置面板的拉黑/解除
    # 端点从此能直接操作QQ运行中的同一实例，修复"面板拉黑重启才生效、
    # 运行实例回写覆盖面板改动"的问题
    # （依据 docs/panel-audit-2026-07-03/panel-users.json）。
    # 原先在 handle_qq_message 里惰性创建（首条消息时），现改为启动即创建，
    # 构造参数与惰性版完全一致，行为不变。
    qq_user_filter = None
    try:
        from white_salary.core.memory.user_filter import UserFilter
        from white_salary.infrastructure.server.settings_api import (
            register_runtime_instance,
        )
        qq_user_filter = UserFilter(
            data_dir=str(_service_root / "data" / "memory"),
            owner_id=str((family_qq or [0])[0]),
            affinity_data_dir=str(_service_root / "data" / "affinity"),
        )
        register_runtime_instance("user_filter", qq_user_filter)
    except Exception as e:
        logger.warning(f"[QQ] 用户过滤器初始化/注册失败（消息将不做用户过滤）: {e}")

    # 消息缓冲器（合并连续消息，2秒窗口）
    msg_buffer = MessageBuffer(wait_timeout=2.0, min_wait=1.0, max_buffer=20, max_total_wait=30.0)
    _buffer_processing: dict[str, bool] = {}  # 记录哪些用户正在处理中
    _decision_raw_parts: dict[str, list[str]] = {}
    _buffer_affinity_event_ids: dict[str, list[str]] = {}

    from white_salary.core.smart_reply import (
        ReplyDecision,
        SmartReplyDecider,
        contains_wake_word,
        normalize_group_ids,
        normalize_wake_words,
    )
    qq_wake_words = normalize_wake_words(wake_words, bot_name=bot_name)
    qq_unblocked_group_ids = normalize_group_ids(unblocked_group_ids)
    from white_salary.core.runtime.engagement import EngagementLeaseBook
    qq_engagement_leases = EngagementLeaseBook(
        _service_root / "data" / "runtime" / "agent_runtime.db"
    )
    qq_smart_decider = SmartReplyDecider(
        bot_self_id="",
        bot_name=bot_name,
        owner_ids=[str(q) for q in (family_qq or [])],
        wake_words=qq_wake_words,
        unblocked_group_ids=qq_unblocked_group_ids,
        engagement_leases=qq_engagement_leases,
    )
    try:
        from white_salary.infrastructure.server.settings_api import (
            register_runtime_instance as _register_rt,
        )
        _register_rt("qq_smart_reply_decider", qq_smart_decider)
        _register_rt("qq_engagement_leases", qq_engagement_leases)
    except Exception as e:
        logger.warning(f"[QQ] 登记SmartReply运行实例失败（设置页即时同步不可用）: {e}")

    def _persist_unblocked_group_ids() -> None:
        """Persist QQ manual unblocked group list to conf.yaml."""
        root = Path(project_root) if project_root is not None else Path.cwd()
        conf_path = root / "conf.yaml"
        try:
            import yaml

            config = {}
            if conf_path.exists():
                config = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
            if not isinstance(config.get("qq"), dict):
                config["qq"] = {}
            config["qq"]["unblocked_group_ids"] = qq_smart_decider.list_unblocked_groups()
            conf_path.write_text(
                yaml.dump(
                    config,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[QQ] 持久化不屏蔽群列表失败: {e}")

    class _MergedQQDecisionMessage:
        """Message-like object for SmartReply after QQ text buffering."""

        def __init__(self, source: QQMessage, raw_message: str) -> None:
            self.is_group = source.is_group
            self.group_id = source.group_id
            self.user_id = source.user_id
            self.raw_message = raw_message or source.raw_message
            self.is_at_me = bool(getattr(source, "is_at_me", False))
            self.has_media = bool(
                getattr(source, "has_image", False)
                or "[CQ:record," in getattr(source, "raw_message", "")
                or "[图片" in self.raw_message
                or "[语音" in self.raw_message
            )

    def _record_qq_reply_for_smart(msg: QQMessage) -> None:
        if msg.is_group:
            qq_smart_decider.record_reply(msg.group_id, msg.user_id)

    def _begin_qq_runtime_task(
        msg: QQMessage,
        request_text: str,
        *,
        event_ids: Optional[list[str]] = None,
        source: str = "message",
    ) -> InteractiveTaskHandle:
        stable_ids = sorted({str(value) for value in (event_ids or []) if value})
        if not stable_ids and getattr(msg, "message_id", 0):
            stable_ids = [f"qq:{msg.message_id}"]
        idempotency_key = ""
        if stable_ids:
            digest = hashlib.sha256("|".join(stable_ids).encode("utf-8")).hexdigest()
            idempotency_key = f"qq-input:{digest}"

        scope = "group" if msg.is_group else "private"
        conversation_id = (
            f"{msg.group_id}:user:{msg.user_id}" if msg.is_group else msg.user_id
        )
        task = runtime_journal.begin(
            ConversationRef(
                platform="qq",
                conversation_id=conversation_id,
                scope=scope,
                user_id=msg.user_id,
                group_id=msg.group_id if msg.is_group else "",
            ),
            request_text,
            owner_id=msg.user_id,
            response_address=ChannelAddress(
                platform="qq",
                address=msg.group_id if msg.is_group else msg.user_id,
                is_group=msg.is_group,
            ),
            metadata={
                "source": source,
                "event_ids": stable_ids,
                "sender_name": msg.sender_name,
            },
            idempotency_key=idempotency_key,
        )
        setattr(msg, "_runtime_task", task)
        return task

    def _on_qq_reply_delivered(msg: QQMessage, message_id: int) -> None:
        if msg.is_group and message_id:
            qq_smart_decider.record_reply(msg.group_id, msg.user_id)
        runtime_task = getattr(msg, "_runtime_task", None)
        if runtime_task is not None and message_id:
            runtime_task.complete(
                receipt={
                    "transport": "napcat",
                    "message_id": int(message_id),
                },
            )

    def _on_qq_reply_failed(msg: QQMessage, error: str) -> None:
        runtime_task = getattr(msg, "_runtime_task", None)
        if runtime_task is not None:
            runtime_task.require_reconciliation(error)

    adapter.on_reply_delivered = _on_qq_reply_delivered
    adapter.on_reply_failed = _on_qq_reply_failed

    def _is_direct_to_bai(msg: QQMessage, raw: str, text: str) -> bool:
        """Whether this QQ message explicitly calls Bai."""
        return (
            not msg.is_group
            or bool(getattr(msg, "is_at_me", False))
            or bool(getattr(msg, "_force_reply", False))
            or contains_wake_word(raw or text, qq_wake_words)
        )

    def _match_group_unblock_command(text: str) -> str | None:
        """Match owner commands for QQ group inactive-gate bypass."""
        normalized = re.sub(r"[\s,，。.!！?？:：;；、~～…“”\"'`]+", "", text or "")
        if not normalized:
            return None

        status_terms = (
            "这个群屏蔽状态", "本群屏蔽状态", "这个群不屏蔽状态", "本群不屏蔽状态",
            "这个群会不会屏蔽", "本群会不会屏蔽",
        )
        enable_terms = (
            "这个群不屏蔽", "本群不屏蔽", "这个群别屏蔽", "本群别屏蔽",
            "这个群不要屏蔽", "本群不要屏蔽", "这个群取消屏蔽", "本群取消屏蔽",
            "这个群放开", "本群放开", "这个群正常回复", "本群正常回复",
            "这个群恢复正常", "本群恢复正常",
        )
        disable_terms = (
            "这个群恢复智能屏蔽", "本群恢复智能屏蔽",
            "这个群开启自动屏蔽", "本群开启自动屏蔽",
            "这个群按活跃判断", "本群按活跃判断",
            "关闭本群不屏蔽", "关闭这个群不屏蔽",
            "这个群取消不屏蔽", "本群取消不屏蔽",
        )

        if any(term in normalized for term in status_terms):
            return "status"
        if any(term in normalized for term in disable_terms):
            return "disable"
        if any(term in normalized for term in enable_terms):
            return "enable"
        return None

    def _handle_group_unblock_command(msg: QQMessage, text: str, raw: str) -> str | None:
        """Handle owner-only manual group inactive-gate commands."""
        if not msg.is_group:
            return None

        action = _match_group_unblock_command(text)
        if action is None:
            return None

        direct = _is_direct_to_bai(msg, raw, text)
        is_owner = msg.user_id in {str(q) for q in (family_qq or [])}
        if not direct and not is_owner:
            return None
        if not is_owner:
            return "这个设置要主人来改。"

        if action == "status":
            if qq_smart_decider.is_group_unblocked(msg.group_id):
                return "这个群现在是不屏蔽状态：我会更容易把群消息交给续聊判断，但还是先判断是不是该接话。"
            return "这个群现在按智能接话判断：没叫我、也不像续聊的时候，我会先观察。"

        if action == "enable":
            qq_smart_decider.set_group_unblocked(msg.group_id, True)
            _persist_unblocked_group_ids()
            logger.info(f"[QQ] 群 {msg.group_id} 已由主人设置为不屏蔽")
            return "这个群已设为不屏蔽：我会更容易把群消息交给续聊判断，但还是先判断是不是该接话。"

        if action == "disable":
            qq_smart_decider.set_group_unblocked(msg.group_id, False)
            _persist_unblocked_group_ids()
            logger.info(f"[QQ] 群 {msg.group_id} 已恢复智能活跃判断")
            return "这个群已恢复智能接话判断：没叫我、也不像续聊时我会先观察。"

        return None

    async def _judge_active_continuation(
        *,
        msg: QQMessage,
        text: str,
        group_context: str,
        smart_reason: str,
        smart_score: float,
    ) -> bool:
        """
        In an active QQ group window, decide if the user is still talking to Bai.

        The active window is only a candidate window; it is not permission to
        reply to unrelated self-talk or other people's conversation.
        """
        if continuation_llm is None:
            return smart_score >= 30.0

        system = (
            "你是QQ群聊回复闸门，只判断“白”是否应该回复当前这条消息。"
            "只输出 JSON：{\"reply\": true/false, \"reason\": \"...\"}。"
            "规则：如果当前消息明确接着白上一轮的话题、是在回答白、或图片/表情/语音与刚才白参与的话题有关，reply=true；"
            "如果用户在和别人说话、自言自语、突然换了无关话题、只是群里闲聊、或不确定，reply=false。"
            "不要因为群处于活跃窗口就默认回复。"
        )
        user = (
            f"白的名字: {bot_name}\n"
            f"发送者: {msg.sender_name} QQ:{msg.user_id}\n"
            f"初筛理由: {smart_reason} / 分数:{smart_score:.0f}\n\n"
            f"最近群聊上下文:\n{group_context[-1600:] if group_context else '(无)'}\n\n"
            f"当前合并消息:\n{text}\n\n"
            "判断白现在该不该接话。"
        )
        try:
            reply = await continuation_llm.chat_completion(
                messages=[
                    Message(role=MessageRole.SYSTEM, content=system),
                    Message(role=MessageRole.USER, content=user),
                ],
                temperature=0.1,
                max_tokens=120,
            )
        except Exception as e:
            logger.debug(f"[QQ] 续聊语义判断失败，使用分数兜底: {e}")
            return smart_score >= 30.0

        return _parse_continuation_reply(
            reply or "",
            fallback=smart_score >= 30.0,
        )

    # 实时多消息拦截：白生成回复期间收到新消息，合并后重新生成
    _generating: dict[str, bool] = {}       # 记录哪些用户的回复正在生成中
    _pending_new_msgs: dict[str, list[str]] = {}  # 生成期间收到的新消息排队

    # 初始化并同步由 qq.family_qq 管理的家人关系。手动设置的家人不受影响；
    # 从配置移除的 config 来源条目会恢复进入家人前的真实好感度。
    _affinity_root = str(
        _service_root / "data"
        / "affinity"
    )
    AffinityManager.sync_configured_family(
        [str(qq_id) for qq_id in (family_qq or [])],
        data_dir=_affinity_root,
    )

    async def handle_qq_message(msg: QQMessage) -> Optional[str]:
        """处理QQ消息，返回回复文本（带完整功能模块）。"""
        runtime_task: Optional[InteractiveTaskHandle] = None
        try:
            # 设置当前消息上下文（让工具知道是群聊还是私聊）
            try:
                from white_salary.adapters.tools.builtin.qq_api import set_msg_context
                set_msg_context(
                    group_id=msg.group_id if msg.is_group else "",
                    user_id=msg.user_id,
                    is_group=msg.is_group,
                )
            except Exception:
                pass

            text = msg.text
            decision_raw = getattr(msg, "raw_message", "") or text
            original_text = text

            # ========== 语音消息识别（ASR）==========
            if asr_adapter and hasattr(msg, 'raw') and '[CQ:record,' in msg.raw.get("raw_message", ""):
                try:
                    import re as _re
                    record_match = _re.search(r'\[CQ:record,file=([^\],]+)', msg.raw.get("raw_message", ""))
                    if record_match:
                        voice_file = record_match.group(1)
                        # 通过QQ API下载语音文件
                        voice_data = await adapter._call_api("get_record", {
                            "file": voice_file, "out_format": "mp3"
                        }, wait_response=True)
                        if voice_data and voice_data.get("file"):
                            from pathlib import Path
                            voice_path = Path(voice_data["file"])
                            if voice_path.exists():
                                asr_text = await _transcribe_qq_voice(
                                    asr_adapter,
                                    voice_path.read_bytes(),
                                    "mp3",
                                )
                                if asr_text:
                                    text = asr_text
                                    logger.info(f"[QQ] 语音识别: {asr_text[:30]}")
                except Exception as e:
                    logger.debug(f"[QQ] 语音识别失败: {e}")
                if not text:
                    text = "[对方发了一条语音消息]"

            # ========== 图片理解（Vision）==========
            if vision_adapter and msg.image_urls:
                try:
                    import aiohttp as _aiohttp
                    import base64 as _b64
                    img_url = msg.image_urls[0]
                    # 下载图片转base64
                    async with _aiohttp.ClientSession() as _sess:
                        async with _sess.get(img_url, timeout=_aiohttp.ClientTimeout(total=10)) as _resp:
                            if _resp.status == 200:
                                img_data = await _resp.read()
                                img_b64 = _b64.b64encode(img_data).decode()
                                description = await vision_adapter.describe_image(img_b64)
                                if description and not description.startswith("["):
                                    img_hint = f"[图片：{description[:100]}]"
                                    text = f"{img_hint} {text}" if text else img_hint
                                    logger.info(f"[QQ] 图片识别: {description[:30]}")
                except Exception as e:
                    logger.debug(f"[QQ] 图片识别失败: {e}")
                if not text:
                    text = "[对方发了一张图片]"

            if not text:
                return None
            decision_part = _compose_decision_raw_part(decision_raw, text, original_text)

            # 群聊里的媒体消息在 adapter 早期只能记录到 [图片]/[语音消息]，
            # 这里把 ASR/视觉后的富文本补进上下文。这样白被唤醒时能看到之前图片
            # 或表情的含义；不代表当前消息一定会触发回复。
            if msg.is_group and text != original_text and (
                bool(getattr(msg, "has_image", False))
                or "[CQ:record," in getattr(msg, "raw_message", "")
            ):
                ctx_manager.add_message(
                    QQContextManager.group_key(msg.group_id),
                    msg.sender_name,
                    text[:300],
                )

            # ========== QQ当前会话停止指令 ==========
            # 私聊、显式呼叫，或当前(group,user)活动窗口内的用户都能立即停止本次会话。
            # 这只关闭该用户在该群的 EngagementLease，绝不改全局在场/静默状态。
            _direct_stop = _is_direct_to_bai(msg, decision_raw, text)
            if _should_consume_stop_request(
                msg=msg,
                text=text,
                is_direct=_direct_stop,
                engagement_leases=qq_engagement_leases,
            ):
                if msg.is_group:
                    qq_engagement_leases.close(
                        QQContextManager.group_key(msg.group_id),
                        msg.user_id,
                        "user_requested_stop",
                    )
                logger.info(
                    f"[QQ] 已停止当前会话回复: {msg.sender_name}({msg.user_id})"
                )
                return None

            # ========== 忙碌/静默模式闸门 ==========
            # 2026-07-03 工具实现（批9）：处理消息前先查在场状态（纯内存判断，不拖慢）。
            # 静默/忙碌时：群聊闲聊直接闭嘴；被@或私聊限频（每用户30分钟）简短告知
            # "我在忙"；主人私聊带紧急词（"紧急/在吗"）或解除意图词（"忙完了"等）
            # 仍走正常回复——解除词放行是为了主人能在QQ上把静默关掉（clear_quiet_mode
            # 工具得有机会被 tool_llm 选中执行），否则设了就解不开。
            # 状态检查异常时保守按正常模式处理（绝不因新功能吞掉消息）。
            _is_owner_msg: bool = msg.user_id in {str(q) for q in (family_qq or [])}
            try:
                _quiet_decision = qq_presence.decide_qq_reply(
                    user_id=msg.user_id,
                    text=text,
                    is_group=msg.is_group,
                    is_at_me=bool(getattr(msg, "is_at_me", False)),
                    is_owner=_is_owner_msg,
                )
            except Exception as _presence_err:
                logger.warning(f"[QQ] 忙碌/静默状态检查失败（按正常模式处理）: {_presence_err}")
                _quiet_decision = None
            if _quiet_decision is not None:
                if _quiet_decision.action == "skip":
                    logger.debug(
                        f"[QQ] 忙碌/静默模式：不回复 {msg.sender_name}({msg.user_id})"
                    )
                    return None
                if _quiet_decision.action == "brief_notice":
                    logger.info(
                        f"[QQ] 忙碌/静默模式：简短告知 {msg.sender_name}({msg.user_id})"
                    )
                    return _quiet_decision.notice_text

            # 持久黑/白名单必须覆盖普通消息和系统事件；此前系统事件快速通道
            # 会绕过过滤，让已拉黑用户仍可用戳一戳/撤回等事件触发回复。
            _is_family = msg.user_id in {str(q) for q in (family_qq or [])}
            if _is_user_blocked(
                qq_user_filter,
                msg.user_id,
                is_family=_is_family,
            ):
                logger.debug(f"[QQ] 用户过滤拦截: {msg.sender_name} ({msg.user_id})")
                return None

            # ========== 系统事件快速通道 ==========
            # 戳一戳/撤回回应/入群欢迎等事件消息，跳过缓冲/社交/过滤等检查
            # 直接走ChatAgent生成回复，不然会被消息缓冲器合并或被社交冷却拒绝
            # 不清空记忆——保持记忆互通，事件回复也有上下文
            # 提示词已标清来源（谁在哪个群），大模型不会搞混
            if getattr(msg, '_is_system_event', False):
                runtime_task = _begin_qq_runtime_task(
                    msg,
                    text,
                    source="system_event",
                )
                if not runtime_task.should_process:
                    return None
                # 设置消息上下文（让工具知道是群聊还是私聊）
                try:
                    from white_salary.adapters.tools.builtin.qq_api import set_msg_context
                    set_msg_context(
                        group_id=msg.group_id if msg.is_group else "",
                        user_id=msg.user_id,
                        is_group=msg.is_group,
                    )
                except Exception:
                    pass
                event_agent = _session_agent_for(msg)
                reply = await event_agent.chat(
                    text, user_name=msg.sender_name,
                    user_id=msg.user_id, is_group=msg.is_group,
                    group_id=msg.group_id if msg.is_group else "",
                )
                if reply:
                    import re as _re
                    reply = _re.sub(r'<[^>]+>', '', reply).strip()
                    runtime_task.response_ready(reply, awaiting_delivery=True)
                    return reply
                runtime_task.complete(
                    "No response generated",
                    receipt={"transport": "none", "reason": "empty_response"},
                )
                return None

            # 冲突检测（打断直接return，修正/补充加hint）
            conflict = qq_conflict_detector.check(text)
            if conflict.has_conflict:
                if conflict.conflict_type == ConflictType.INTERRUPT:
                    return None
                elif conflict.conflict_type in (ConflictType.CORRECTION, ConflictType.SUPPLEMENT):
                    text = f"{conflict.hint}\n{text}"

            # 2026-07-02 审计修复（批4）：重投递消息标记——重生成达上限后被
            # 静默丢弃的排队消息会以 _RedeliveredMsg 重新进入本入口；
            # 这类消息跳过缓冲与拦截合并，直接普通处理一轮（防死循环）
            _is_redelivery: bool = bool(getattr(msg, '_is_redelivered', False))
            affinity_event_ids: list[str] = []

            # ========== 消息缓冲（合并连续消息，2秒窗口） ==========
            buffer_key = f"{msg.group_id}_{msg.user_id}" if msg.is_group else msg.user_id
            if not _is_redelivery:
                _decision_raw_parts.setdefault(buffer_key, []).append(decision_part)
                if msg.message_id:
                    _buffer_affinity_event_ids.setdefault(buffer_key, []).append(
                        f"qq:{msg.message_id}"
                    )
                msg_buffer.add(buffer_key, text)

                # 如果这个用户已经有handler在等buffer → 消息已加入缓冲，直接return
                if _buffer_processing.get(buffer_key):
                    logger.debug(f"[QQ] 消息已缓冲: {text[:20]}")
                    return None

                # 第一条消息，开始等待buffer flush
                _buffer_processing[buffer_key] = True
                try:
                    merged = await msg_buffer.wait_and_flush(buffer_key)
                    if not merged:
                        _decision_raw_parts.pop(buffer_key, None)
                        _buffer_affinity_event_ids.pop(buffer_key, None)
                        return None
                    text = merged
                    decision_raw = "\n".join(
                        _decision_raw_parts.pop(buffer_key, [])
                    ) or text
                    affinity_event_ids = _buffer_affinity_event_ids.pop(buffer_key, [])
                finally:
                    _buffer_processing[buffer_key] = False
            else:
                decision_raw = text
            # ========== 缓冲结束，text是合并后的完整消息 ==========

            if msg.is_group and not _is_redelivery and (
                "\n" in text or text != original_text
            ):
                ctx_manager.add_message(
                    QQContextManager.group_key(msg.group_id),
                    msg.sender_name,
                    text,
                )

            group_unblock_reply = _handle_group_unblock_command(msg, text, decision_raw)
            if group_unblock_reply is not None:
                ctx_manager.add_message(
                    QQContextManager.group_key(msg.group_id),
                    bot_name,
                    group_unblock_reply[:100],
                )
                return group_unblock_reply

            _plugin_mgr = None
            _plugin_metadata = None
            _plugin_observed = False
            try:
                _plugin_mgr = _get_plugin_manager()
                if _plugin_mgr is not None:
                    _plugin_metadata = {
                        "platform": "qq",
                        "is_group": msg.is_group,
                        "group_id": msg.group_id if msg.is_group else "",
                        "user_id": msg.user_id,
                        "sender_name": msg.sender_name,
                        "is_at_me": bool(getattr(msg, "is_at_me", False)),
                        "phase": "observe",
                    }
                    await _plugin_mgr.observe_message(
                        text,
                        msg.user_id,
                        metadata=_plugin_metadata,
                    )
                    _plugin_observed = True
            except Exception as _pe:
                logger.warning(f"[QQ] 插件 observer 钩子异常，已跳过观察: {_pe}")

            # ========== QQ群聊回复决策（合并后再判断） ==========
            if msg.is_group and not _is_redelivery:
                qq_smart_decider.record_user_response(msg.group_id, msg.user_id)
                if not bool(getattr(msg, "_force_reply", False)):
                    smart_result = qq_smart_decider.decide(
                        _MergedQQDecisionMessage(msg, decision_raw)
                    )
                    if smart_result.decision == ReplyDecision.SEMANTIC_CHECK:
                        group_ctx_for_gate = ctx_manager.get_context(
                            QQContextManager.group_key(msg.group_id)
                        )
                        should_continue = await _judge_active_continuation(
                            msg=msg,
                            text=text,
                            group_context=group_ctx_for_gate,
                            smart_reason=smart_result.reason,
                            smart_score=smart_result.score,
                        )
                        if not should_continue:
                            qq_smart_decider.record_unrelated_message(
                                msg.group_id,
                                msg.user_id,
                            )
                            logger.debug(
                                f"[QQ] 活跃窗口语义判断不接话 {msg.sender_name}({msg.user_id}): "
                                f"{smart_result.reason}"
                            )
                            return None
                        qq_smart_decider.record_relevant_followup(
                            msg.group_id,
                            msg.user_id,
                        )
                    elif smart_result.decision != ReplyDecision.REPLY:
                        logger.debug(
                            f"[QQ] SmartReply不回复 {msg.sender_name}({msg.user_id}): "
                            f"{smart_result.reason}"
                        )
                        return None

            # ========== 实时多消息拦截 ==========
            # 如果白正在为这个用户生成回复，新消息先排队，等生成完了合并重新生成
            # 2026-07-02 审计修复（批4）：重投递消息不进拦截队列（防死循环）
            if not _is_redelivery and _generating.get(buffer_key):
                _pending_new_msgs.setdefault(buffer_key, []).append(text)
                logger.debug(f"[QQ] 白正在回复中，消息已排队等合并: {text[:30]}")
                return None

            # 社交系统检查（家人跳过，不受冷却/黑名单限制）
            if not _is_family:
                if not qq_social.should_process(msg.user_id, text, msg.is_group):
                    logger.debug(f"[QQ] 社交系统拦截: {msg.sender_name} ({msg.user_id})")
                    return None
            qq_social.on_message(msg.user_id, text, msg.is_group)

            # 隐私守卫（群聊防套私聊内容）
            if msg.is_group:
                try:
                    from white_salary.core.memory.privacy_guard import PrivacyGuard
                    if not hasattr(handle_qq_message, '_privacy_guard'):
                        handle_qq_message._privacy_guard = PrivacyGuard(
                            owner_id=str((family_qq or [0])[0])
                        )
                    privacy_result = handle_qq_message._privacy_guard.check(
                        text, user_id=msg.user_id, is_group=True,
                        owner_id=str((family_qq or [0])[0]),
                    )
                    if privacy_result.blocked:
                        text = f"[系统提示：{privacy_result.prompt}]\n用户消息：{text}"
                except Exception as _e:
                        logger.debug(f'[QQ] 静默异常: {_e}')

            # 休息系统检查（休息中不回，除非@了或者是家人）
            # _is_family 已在社交检查处定义
            # 2026-07-03 面板升级（批6）：开关关闭时 qq_rest 为 None，跳过作息检查
            if qq_rest is not None and qq_rest.is_resting and not msg.is_at_me and not _is_family:
                return None

            # 时间感知
            qq_time.record_interaction(msg.user_id)

            # 话题追踪（2026-07-03 面板升级（批6）：开关关闭时为 None，判空跳过）
            if qq_topic_tracker is not None:
                qq_topic_tracker.record_message(text, source="user")

            # 上下文（群聊由on_group_record记录所有消息，这里只记私聊的）
            context_id = (
                QQContextManager.group_key(msg.group_id)
                if msg.is_group
                else QQContextManager.private_key(msg.user_id)
            )
            if not msg.is_group:
                ctx_manager.add_message(context_id, msg.sender_name, text)

            runtime_task = _begin_qq_runtime_task(
                msg,
                text,
                event_ids=affinity_event_ids,
                source="redelivery" if _is_redelivery else "message",
            )
            if not runtime_task.should_process:
                logger.info(
                    f"[Runtime] 忽略重复QQ消息 task={runtime_task.id}"
                )
                return None

            # 处理多用户好感度（只执行一次，不需要随重新生成重复）
            user_aff = AffinityManager.get_for_user(
                msg.user_id,
                data_dir=_affinity_root,
            )
            user_aff.process_event(text, affinity_event_ids)

            # 用户学习
            if user_learning:
                user_learning.on_message(msg.user_id, msg.sender_name, text)
                if user_learning.should_learn(msg.user_id):
                    try:
                        await user_learning.learn(msg.user_id)
                    except Exception as e:
                        logger.debug(f"[QQ] 用户学习失败: {e}")

            # QQ空间社交联动（学习用户兴趣+触发逛空间）
            try:
                from white_salary.core.qzone.social_manager import get_social_manager
                _qzone_mgr = get_social_manager()
                if _qzone_mgr.on_chat_message(
                    msg.user_id, msg.sender_name, text,
                    quality="normal",
                ):
                    # 兴趣达标，异步逛空间（不阻塞当前回复）
                    asyncio.create_task(
                        _qzone_mgr.trigger_visit_async(msg.user_id, msg.sender_name)
                    )
            except Exception as _e:
                logger.debug(f"[QQ] QZone社交联动异常: {_e}")

            # ========== 插件消息钩子（抢答）==========
            # 2026-07-03 功能大项（批11）：LLM 生成前先让插件处理消息。
            # on_message 型插件（daily_fortune/coin_flip 等）返回非空 → 直接
            # 用它作为回复、跳过整段 LLM 生成（"抢答"）；返回空则正常走 LLM。
            # 钩子调用整体由 PluginManager.process_message 内部 try/except+超时
            # 兜底（单个坏插件不拖垮消息链路），这里再包一层保险：出任何岔子都
            # 当作"无插件拦截"继续走 LLM。插件仍受上面所有社交/过滤/静默门槛约束。
            if _plugin_mgr is None:
                _plugin_mgr = _get_plugin_manager()
            if _plugin_mgr is not None:
                try:
                    if _plugin_metadata is None:
                        _plugin_metadata = {
                            "platform": "qq",
                            "is_group": msg.is_group,
                            "group_id": msg.group_id if msg.is_group else "",
                            "user_id": msg.user_id,
                            "sender_name": msg.sender_name,
                            "is_at_me": bool(getattr(msg, "is_at_me", False)),
                        }
                    _plugin_metadata["phase"] = "intercept"
                    if not _plugin_observed:
                        await _plugin_mgr.observe_message(
                            text,
                            msg.user_id,
                            metadata={**_plugin_metadata, "phase": "observe"},
                        )
                    _plugin_reply = await _plugin_mgr.process_message(
                        text,
                        msg.user_id,
                        metadata=_plugin_metadata,
                    )
                except Exception as _pe:
                    logger.warning(f"[QQ] 插件 on_message 钩子异常，走正常LLM流程: {_pe}")
                    _plugin_reply = None
                if _plugin_reply:
                    logger.info(f"[QQ] 插件抢答 {msg.sender_name}: {_plugin_reply[:30]}")
                    # 记录到上下文并直接返回（不进 LLM）
                    ctx_manager.add_message(context_id, bot_name, _plugin_reply[:100])
                    runtime_task.response_ready(
                        _plugin_reply,
                        awaiting_delivery=True,
                    )
                    return _plugin_reply

            # ========== 生成回复（带实时多消息拦截） ==========
            # 白生成回复期间如果收到新消息，生成完后丢掉旧回复，合并重新生成
            # 最多重新生成2次，防止对方一直发消息导致无限循环
            # 2026-07-02 审计修复（批4）：重投递消息不注册 _generating/不碰排队队列
            # （它可能与同 key 的正常生成并发，碰共享队列会偷走/清掉别轮的消息），
            # 且只普通处理一轮（_max_regen=0）
            if not _is_redelivery:
                _generating[buffer_key] = True
                _pending_new_msgs.pop(buffer_key, None)  # 清空旧排队
            _max_regen = 0 if _is_redelivery else 2  # 最多重新生成2次
            leftover_pending: list[str] = []  # 达重生成上限后仍在排队的消息（重投递用）
            session_agent = _session_agent_for(msg)
            execution_lock = agent_sessions.execution_lock(
                qq_conversation_key(
                    user_id=msg.user_id,
                    group_id=msg.group_id if msg.is_group else "",
                    is_group=msg.is_group,
                )
            )
            lock_acquired = False

            try:
                await execution_lock.acquire()
                lock_acquired = True
                for _regen_round in range(_max_regen + 1):
                    # 构建输入（每次重新生成都要重新构建，因为text可能变了）
                    if msg.is_group:
                        user_input = f"[QQ群消息 群号:{msg.group_id} 发送者:{msg.sender_name} QQ:{msg.user_id}]\n{text}"
                    else:
                        user_input = f"[QQ私聊 发送者:{msg.sender_name} QQ:{msg.user_id}]\n{text}"

                    if msg.is_group:
                        group_ctx = ctx_manager.get_context(context_id)
                        if group_ctx:
                            user_input = f"{group_ctx}\n\n{msg.sender_name} 对你说: {text}"

                    time_ctx = qq_time.get_time_context(msg.user_id)
                    if time_ctx:
                        user_input = (
                            f"[时间] {time_ctx}\n"
                            "[回复约束] 当前时间只供你理解作息和上下文；除非用户询问时间、"
                            "讨论作息/日期，或话题确实需要，否则不要主动说“现在是晚上/下午/几点”。\n"
                            f"{user_input}"
                        )

                    # 2026-07-03 面板升级（批6）：话题追踪开关关闭时不注入提示
                    topic_hint = qq_topic_tracker.get_hint() if qq_topic_tracker is not None else ""
                    if topic_hint:
                        user_input = f"{topic_hint}\n{user_input}"

                    enhance = qq_enhancer.analyze(text)
                    if enhance.style_hint:
                        user_input = f"[意图] {enhance.style_hint}\n{user_input}"

                    # 2026-07-02 审计修复（批4）：chat前快照记忆对象引用——
                    # 撤销时按对象身份删除本轮写入的一问一答，不再盲目pop末两条
                    # （快照列表必须保持强引用到撤销完成，防止 id() 复用误判）
                    _ctx_snapshot = list(session_agent._memory._messages)
                    _before_ids = {id(m) for m in _ctx_snapshot}

                    # 调大模型生成回复
                    reply_parts = []
                    async for chunk in session_agent.chat_stream_with_tools(
                        user_input, user_name=msg.sender_name,
                        user_id=msg.user_id, is_group=msg.is_group,
                        group_id=msg.group_id if msg.is_group else "",
                        route_text=text,
                        tool_context={
                            "platform": "qq",
                            "permissions": (
                                ["owner"] if msg.user_id in {str(q) for q in (family_qq or [])}
                                else []
                            ),
                            "allow_side_effects": msg.user_id in {str(q) for q in (family_qq or [])},
                        },
                        runtime_store=runtime_store,
                        runtime_task_id=runtime_task.id,
                    ):
                        reply_parts.append(chunk)
                    reply = "".join(reply_parts)
                    if "__WHITE_SALARY_TOOL_SILENT__" in reply:
                        logger.info("[QQ] 工具已完成且要求静默，本轮不发送额外文字")
                        _record_silent_tool_completion(
                            ctx_manager=ctx_manager,
                            context_id=context_id,
                            bot_name=bot_name,
                            msg=msg,
                            record_reply=_record_qq_reply_for_smart,
                        )
                        runtime_task.complete(
                            "Tool completed without an extra text reply",
                            receipt={"transport": "tool", "silent": True},
                        )
                        return None

                    # ===== 检查排队：生成期间有没有新消息进来 =====
                    # （重投递轮不碰共享队列，见上方说明）
                    pending = [] if _is_redelivery else _pending_new_msgs.pop(buffer_key, [])
                    if pending and _regen_round < _max_regen:
                        # 有新消息！丢掉这次回复，合并后重新生成
                        logger.info(f"[QQ] 生成期间收到{len(pending)}条新消息，合并重新生成")
                        # 2026-07-02 审计修复（批4）：撤销本轮生成写入记忆的一问一答——
                        # 按对象身份+内容/来源标记匹配删除，找不到就不删，
                        # 防止多用户并发时误删他人对话（原pop末两条会删到并发写入的记录）
                        removed = _undo_generation_pair(
                            session_agent._memory._messages,
                            _before_ids,
                            user_input,
                            _expected_memory_tag(
                                msg.sender_name,
                                msg.group_id if msg.is_group else "",
                                msg.is_group,
                            ),
                        )
                        if removed < 2:
                            logger.debug(
                                f"[QQ] 重生成撤销：仅删除{removed}条"
                                f"（未匹配到的按并发安全原则保留不删）"
                            )
                        del _ctx_snapshot  # 撤销完成，释放快照引用
                        # 合并：原消息 + 新消息
                        text = text + "\n" + "\n".join(pending)
                        continue  # 回到循环顶部重新生成
                    else:
                        # 2026-07-02 审计修复（批4）：已达重生成上限时pending非空——
                        # 不再静默丢弃，记下来在本轮回复发出后重投递处理
                        leftover_pending = pending
                        break  # 没有排队消息，或已达重新生成上限，结束循环

            finally:
                if lock_acquired:
                    execution_lock.release()
                if not _is_redelivery:
                    _generating.pop(buffer_key, None)
                    _pending_new_msgs.pop(buffer_key, None)

            # 2026-07-02 审计修复（批4）：重生成达上限后排队的消息重投递回
            # 消息处理入口（原先被静默丢弃：不回复也不进记忆，用户视角"已读不回"）。
            # 重投递的消息带 _is_redelivered 标记，不再触发拦截合并，只普通处理一轮
            if leftover_pending:
                logger.info(
                    f"[QQ] 重生成达上限，{len(leftover_pending)}条排队消息将重投递处理"
                )
                asyncio.create_task(
                    _redeliver_pending(msg, "\n".join(leftover_pending))
                )

            # 休息系统检测（AI回复中如果说"我要休息了"，进入休息状态）
            # 2026-07-03 面板升级（批6）：开关关闭时 qq_rest 为 None，跳过检测
            if qq_rest is not None:
                qq_rest.check_ai_reply(reply)

            # 清理回复
            from white_salary.utils.text import strip_xml_tags
            clean_reply = strip_xml_tags(reply)

            # 机器话过滤（去掉说教/波浪号/重复语气词）
            try:
                from white_salary.core.memory.human_like_filter import HumanLikeFilter
                if not hasattr(handle_qq_message, '_human_filter'):
                    handle_qq_message._human_filter = HumanLikeFilter()
                clean_reply = handle_qq_message._human_filter.filter_response(clean_reply)
            except Exception as _e:
                    logger.debug(f'[QQ] 静默异常: {_e}')

            # ========== 插件回复钩子（改写）==========
            # 2026-07-03 功能大项（批11）：AI 回复发出前让 on_reply 型插件有机会
            # 改写内容（如加签名/替换敏感词）。process_reply 内部对每个插件都有
            # SafeExecutor 兜底（异常/超时返回上一版），顶层也有 try/except；
            # 这里再包一层：出任何岔子都保留过滤后的原回复。独立取一次管理器实例
            # （不依赖上面抢答段的局部变量，防止代码路径变动导致未定义）。
            _reply_plugin_mgr = _get_plugin_manager()
            if _reply_plugin_mgr is not None:
                try:
                    clean_reply = await _reply_plugin_mgr.process_reply(clean_reply)
                except Exception as _pre:
                    logger.warning(f"[QQ] 插件 on_reply 钩子异常，保留原回复: {_pre}")

            if not clean_reply.strip():
                clean_reply = "刚才脑袋空了一下，你再说一遍？"

            # 记录AI回复到上下文
            ctx_manager.add_message(context_id, bot_name, clean_reply[:100])

            # 写入跨平台对话日志
            try:
                from white_salary.core.memory.conversation_log import ConversationLog
                conv_log = ConversationLog.get_instance()
                conv_log.record(
                    platform="qq",
                    user_name=msg.sender_name,
                    user_id=msg.user_id,
                    group_id=msg.group_id if msg.is_group else "",
                    user_msg=text,
                    ai_reply=clean_reply,
                )
            except Exception as _e:
                    logger.debug(f'[QQ] 静默异常: {_e}')

            # 表情包处理：显式<sticker>必发；普通轻松QQ回复按策略概率附带
            import re as _re
            from white_salary.adapters.platform.sticker_policy import QQStickerPolicy
            if not hasattr(handle_qq_message, '_sticker_policy'):
                handle_qq_message._sticker_policy = QQStickerPolicy(probability=0.5)
            should_attach_sticker = handle_qq_message._sticker_policy.should_attach(
                reply,
                clean_reply,
                text,
                is_group=msg.is_group,
            )
            if should_attach_sticker:
                try:
                    from white_salary.adapters.platform.sticker_manager import StickerManager
                    if not hasattr(handle_qq_message, '_sticker_mgr'):
                        handle_qq_message._sticker_mgr = StickerManager(data_dir="data")
                        handle_qq_message._sticker_mgr.init()
                    cq = handle_qq_message._sticker_mgr.to_cq_random()
                    if cq:
                        clean_reply = clean_reply + "\n" + cq
                except Exception as _e:
                        logger.debug(f'[QQ] 静默异常: {_e}')
            # 清除<sticker>标签本身（strip_xml_tags通常已清理，这里兜底）
            clean_reply = _re.sub(r'<sticker>.*?</sticker>', '', clean_reply).strip()

            if not clean_reply.strip():
                clean_reply = "刚才脑袋空了一下，你再说一遍？"

            logger.info(
                f"[QQ] {'群' if msg.is_group else '私'}聊 "
                f"{msg.sender_name}: {text[:30]} → {clean_reply[:30]}"
            )

            runtime_task.response_ready(clean_reply, awaiting_delivery=True)

            return clean_reply

        except Exception as e:
            import traceback
            if runtime_task is not None:
                runtime_task.fail(str(e))
            logger.error(f"[QQ] 回复生成失败: {e}\n{traceback.format_exc()}")
            return None

    async def _redeliver_pending(original: QQMessage, merged_text: str) -> None:
        """
        2026-07-02 审计修复（批4）：把重生成上限后剩余的排队消息重投递处理一轮。

        模拟 adapter._handle_message 的"处理→发送"流程：生成回复后
        直接经 adapter.send_reply 发出（重投递不再走 WebSocket 事件入口）。

        Args:
            original: 触发本轮生成的原始消息（提供回复目标）
            merged_text: 剩余排队消息合并后的文本
        """
        try:
            fake = _RedeliveredMsg(original, merged_text)
            reply = await handle_qq_message(fake)
            if reply:
                await adapter.send_reply(fake, reply)
        except Exception as e:
            logger.warning(f"[QQ] 排队消息重投递失败: {e}")

    adapter.on_message = handle_qq_message

    # 2026-07-02 审计修复（批4）：StartupChecker 提升为 start_qq_service 作用域单例——
    # 原先每次 NapCat 重连 _on_connected 都新建实例，_last_check_time 归零，
    # "30分钟内重连不重复检查"防重形同虚设（重连风暴时每次都全量拉历史+调LLM补发）
    startup_checker = None
    try:
        from white_salary.core.services.startup_checker import StartupChecker
        startup_checker = StartupChecker(
            adapter=adapter,
            agent=agent,
            bot_name=bot_name,
            family_qq=[str(q) for q in (family_qq or [])],
            wake_words=wake_words,
        )
    except Exception as e:
        logger.warning(f"[QQ] StartupChecker 初始化失败，离线消息检查不可用: {e}")

    # 离线消息自动回复（WebSocket连接后在独立task里检查）
    async def _on_connected():
        """WebSocket连接成功后启动离线消息检查（复用同一checker实例，防重生效）。"""
        if startup_checker is None:
            return
        try:
            await startup_checker.check_and_reply()
        except Exception as e:
            logger.debug(f"[QQ] 离线消息检查失败: {e}")

    adapter.on_connected = _on_connected

    async def _qq_bridge_loop() -> None:
        """Consume durable desktop-to-QQ deliveries after NapCat is connected."""
        from white_salary.core.cross_platform import CrossPlatformBridge

        bridge = CrossPlatformBridge()
        default_owner = str((family_qq or [""])[0])
        while True:
            await asyncio.sleep(0.5)
            ws = getattr(adapter, "_ws", None)
            if ws is None or bool(getattr(ws, "closed", False)):
                continue
            messages = bridge.claim_qq_messages(limit=20)
            for item in messages:
                target_id = str(item.get("target_id") or default_owner).strip()
                text = str(item.get("message") or "").strip()
                is_group = bool(item.get("is_group", False))
                if not target_id or not text:
                    bridge.reject_message(item, "QQ bridge target or message is empty")
                    continue
                try:
                    if is_group:
                        message_id = await adapter.send_group_message(target_id, text)
                    else:
                        message_id = await adapter.send_private_message(target_id, text)
                except Exception as e:
                    bridge.mark_message_unknown(item, str(e))
                    continue
                if message_id:
                    bridge.ack_message(
                        item,
                        receipt={
                            "consumer": "qq_adapter",
                            "message_id": int(message_id),
                            "target_id": target_id,
                            "is_group": is_group,
                        },
                    )
                else:
                    bridge.mark_message_unknown(
                        item,
                        "NapCat returned no message_id; delivery outcome is ambiguous",
                    )

    logger.info(f"[QQ] 启动QQ服务: {ws_url}")
    qq_bridge_task = asyncio.create_task(_qq_bridge_loop(), name="qq-bridge-consumer")
    try:
        await adapter.connect()
    finally:
        qq_bridge_task.cancel()
        try:
            await qq_bridge_task
        except asyncio.CancelledError:
            pass
