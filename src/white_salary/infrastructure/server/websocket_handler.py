"""
white_salary/infrastructure/server/websocket_handler.py

WebSocket handler with cancellable chat, voice input, and sentence-level TTS.

Key feature: new messages cancel any in-progress LLM/TTS processing.

2026-07-02 审计修复（批3）整体改造：
  1. 真流式 —— 切句移进 LLM chunk 接收循环（_drain_sentences 增量切句），
     凑齐一句立即下发 sentence、TTS worker 按句序合成 sentence_audio；
     不再等整个 LLM 流结束才切句（原先首句延迟=全文生成时长）。
  2. 可打断 —— 回复处理转入后台 asyncio.Task，接收循环持续 receive_text，
     interrupt/新消息随时能取消进行中的回复（原先串行 await，打断永远收不到）。
  3. reply_start 协议 —— 每轮回复开始、第一条 sentence 之前发
     {"type": "reply_start", "source": "user"|"auto"}，前端据此重置播放队列/游标。
  4. 陈旧令牌修复 —— 回复完成后释放 current_token/current_task；
     冲突检测只在确有回复进行中时才走打断/撤回逻辑（修「等等/慢着」被静默吞掉）。
  5. auto_chat/跨平台桥回复纳入统一任务管理 —— 共用同一份令牌/任务引用，
     interrupt 可取消主动播报；主动回复与用户回复互斥，用户消息优先。
  6. 称呼修复 —— 不再硬编码称呼用户"主人"，_resolve_owner_name 三级解析
     （conf.yaml qq.owner_name → 用户画像 user_name → None 交给模型自然称呼）。
"""

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.interfaces.asr import ASRInterface
from white_salary.core.interfaces.tts import TTSInterface
from white_salary.core.interfaces.types import AudioData
from white_salary.adapters.vision.multimodal_adapter import MultimodalVisionAdapter
from white_salary.core.filter.content_filter import ContentFilter
from white_salary.utils.text import extract_emotion_tags, strip_action_tags, strip_xml_tags, is_valid_for_tts
# 2026-07-02 审计修复（批2）：语音输入格式探测+ffmpeg转码（WebM/Opus → 真WAV）
from white_salary.utils.audio_convert import convert_to_wav, detect_audio_format

if TYPE_CHECKING:
    from white_salary.core.services.user_learning import UserLearningService


def _load_features(project_root: Optional[Path] = None) -> "FeaturesConfig":
    """
    2026-07-03 面板升级（批6）：读取合并配置的 features 节（模块装配期读一次）。

    走 load_config()（conf.default.yaml 深合并 conf.yaml + Pydantic 校验），
    不依赖 CWD；读取失败时保守回退全默认值（全 True = 原硬编码行为）。

    Args:
        project_root: 显式指定项目根目录（主要供单测注入）；None=自动探测

    Returns:
        FeaturesConfig 功能开关配置
    """
    from white_salary.infrastructure.config.models import FeaturesConfig
    try:
        from white_salary.infrastructure.config import load_config
        return load_config(project_root=project_root).features
    except Exception as e:
        logger.warning(f"[WS] features 配置读取失败，回退全默认开关(全开): {e}")
        return FeaturesConfig()


def _make_content_filter(enabled: bool) -> ContentFilter:
    """2026-07-03 面板升级（批6）：按开关构造内容过滤器（False=只记录不过滤）。"""
    return ContentFilter(enabled=enabled)


from white_salary.core.topic_tracker import TopicTracker


def _make_topic_tracker(enabled: bool) -> Optional[TopicTracker]:
    """
    2026-07-03 面板升级（批6）：按开关构造话题追踪器。

    Args:
        enabled: features.topic_tracker 开关

    Returns:
        开关开=TopicTracker实例（原行为）；开关关=None（调用点判空跳过）
    """
    return TopicTracker() if enabled else None


# 2026-07-03 面板升级（批6）：模块级实例改为装配期读一次 conf 决定——
# 修复 features.content_filter / features.topic_tracker 两个面板开关零消费方
# 的问题（依据 docs/panel-audit-2026-07-03/panel-chatcfg.json）。
# 默认（配置缺失/全True）行为与原硬编码完全一致。
_module_features = _load_features()

# Shared content filter for WebSocket responses
_content_filter = _make_content_filter(_module_features.content_filter)

# 话题追踪器（防重复话题；开关关闭时为 None）
_topic_tracker: Optional[TopicTracker] = _make_topic_tracker(_module_features.topic_tracker)


def _get_plugin_manager():
    """
    2026-07-03 功能大项（批11）：从 settings_api 运行实例注册表取 PluginManager。

    run_server 创建 PluginManager 后经 register_runtime_instance('plugins', ...)
    登记；桌面链路的 on_message 抢答 / on_reply 改写从这里取用。取不到返回
    None（未注册/注册表异常），调用方跳过钩子按原流程走，绝不因此报错。

    Returns:
        PluginManager 实例或 None
    """
    try:
        from white_salary.infrastructure.server.settings_api import get_runtime_instance
        return get_runtime_instance("plugins")
    except Exception as e:
        logger.debug(f"[WS] 取插件管理器失败（跳过插件钩子）: {e}")
        return None


def _presence_is_quiet() -> bool:
    """
    2026-07-03 工具实现（批9）：查询忙碌/静默状态。

    set_quiet_mode 工具写入的 PresenceState 在这里被桌面端消费。
    状态模块异常时保守返回 False（=不静默，维持原行为，不误吞主动播报）。
    """
    try:
        from white_salary.core.services.presence_state import PresenceState
        return PresenceState.get_instance().is_quiet
    except Exception:
        return False


def _should_skip_proactive(ignore_quiet: bool = False) -> bool:
    """
    2026-07-03 工具实现（批9）：主动搭话（auto_chat/桥播报）是否因忙碌/静默跳过。

    检查点放在触发处（_auto_chat_send 回调），不动 AutoChatManager 内部；
    用户主动发的消息不经此判断（桌面是主人本人，正常回复不受影响）。

    Args:
        ignore_quiet: True=豁免检查（提醒到点等用户明确要求的通知穿透静默）

    Returns:
        True=应跳过本次主动发言
    """
    if ignore_quiet:
        return False
    return _presence_is_quiet()


def _partition_bridge_messages(messages: list[dict]) -> tuple[list[str], list[str]]:
    """
    2026-07-03 工具实现（批9）：把跨平台桥消息分成「提醒类」与「普通播报」两组提示片段。

    穿透静默的两类（第一组）：
      - source=="reminder"：用户明确设的提醒到点，不算"主动搭话"，静默期也要通知。
      - source=="game"：游戏事件触发的道喜/吐槽（批11 游戏对接）——是对用户当下
        操作的即时回应，静默期也应照常出现，否则打赢 Boss 白却不吭声，体验割裂。
    其余（source=="qq" 等普通转发播报）归第二组，静默期间跳过。纯函数便于单测。

    Args:
        messages: CrossPlatformBridge.pop_desktop_messages() 的结果

    Returns:
        (穿透静默的提示片段列表, 普通播报提示片段列表)
    """
    passthrough_parts: list[str] = []
    normal_parts: list[str] = []
    for msg in messages:
        text = msg.get("message", "")
        if not text:
            continue
        source = msg.get("source", "qq")
        if source == "reminder":
            passthrough_parts.append(f"你之前设的提醒到点了，请自然地转告用户：{text}")
        elif source == "game":
            # 2026-07-03 功能大项（批11）：游戏事件提示已是"翻译好的一句自然话术"，
            # 直接作为触发提示交给白（穿透静默），让她对玩家刚发生的游戏事件即时回应
            passthrough_parts.append(text)
        else:
            normal_parts.append(f"收到来自{source}的消息：{text}")
    return passthrough_parts, normal_parts


_SENTENCE_END = re.compile(r"[。！？!?…\n]")

# 2026-07-02 审计修复（批3）：LLM 流式超时秒数抽成模块常量（便于单测注入短超时）
_LLM_STREAM_TIMEOUT: float = 120.0


class CancellationToken:
    """Simple cancellation flag for interrupting in-progress work."""
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def _drain_sentences(buffer: str) -> tuple[list[str], str]:
    """
    2026-07-02 审计修复（批3）：增量切句纯函数（真流式的核心）。

    从 buffer 头部按 _SENTENCE_END（中英句尾标点/省略号/换行）切出所有
    【完整】句子。设计成纯函数：chunk 接收循环每追加一个 chunk 就调用一次，
    切出的完整句子立即下发，实现"凑齐一句立即播"的句级流式；同时便于单测。

    Args:
        buffer: 当前已累积的文本（可能含 0..N 个完整句子 + 半句残留）

    Returns:
        (sentences, rest)：sentences 为切出的完整句子列表（已 strip、剔除
        纯空白句），rest 为尚未凑满一句的残留文本（可为空串）
    """
    sentences: list[str] = []
    while True:
        match = _SENTENCE_END.search(buffer)
        if match is None:
            break
        end_pos = match.end()
        sentence = buffer[:end_pos].strip()
        buffer = buffer[end_pos:]
        if sentence:
            sentences.append(sentence)
    return sentences, buffer


def _get_emotion_speed_multiplier(agent: ChatAgent) -> float:
    """
    2026-07-03 面板升级（批6）：取当前情绪对应的TTS语速倍率。

    从 agent 的记忆管理器取情绪追踪器（与表情下发同一来源），读
    get_tts_modifiers() 的 speed_factor——"表情动作"页情绪调速表描述的
    链路从此接通（依据 docs/panel-audit-2026-07-03/panel-expressions.json：
    原先该值全仓库仅统计展示用，无TTS链路消费）。

    Args:
        agent: 当前会话的 ChatAgent

    Returns:
        语速倍率（1.0=不调速；追踪器缺失/取值异常时保守返回1.0=原行为）
    """
    try:
        manager = getattr(agent, "_memory_manager", None)
        if manager is None:
            return 1.0
        tracker = getattr(manager, "_emotion_tracker", None)
        if tracker is None:
            tracker = getattr(manager, "emotion", None)
        if tracker is None or not hasattr(tracker, "get_tts_modifiers"):
            return 1.0
        value = float(tracker.get_tts_modifiers().get("speed_factor", 1.0))
        # 防御：倍率必须是正数，否则回退不调速
        return value if value > 0 else 1.0
    except Exception:
        return 1.0


def _resolve_owner_id(conf_path: Optional[Path] = None) -> str:
    """
    2026-07-02 审计修复（批2）：跨平台身份统一。

    从 conf.yaml 的 qq.family_qq 取第一个 QQ 号作为主人的统一 user_id，
    使桌面端与 QQ 端共用同一份好感度/画像/学习状态/对话日志身份
    （此前桌面端硬编码 user_id="desktop"，与 QQ 端的主人 QQ 号是两套账，
    在 QQ 积累的"家人"好感度桌面端完全无感）。

    Args:
        conf_path: 显式指定配置文件路径（主要供测试用）；不传则先按
                   当前工作目录找 conf.yaml，再回退项目根目录下的 conf.yaml

    Returns:
        主人的统一 user_id（family_qq 第一个号的字符串形式）；
        family_qq 为空或配置读取失败时回退旧值 "desktop"
    """
    try:
        import yaml

        if conf_path is not None:
            candidates = [Path(conf_path)]
        else:
            # 2026-07-03 审计修复（批5）：项目根绝对路径提到首位（原来 CWD 相对
            # 路径在前，从其它工作目录启动会读错/读不到配置）；CWD 相对路径保留
            # 作兜底（依据 docs/audit-2026-07-02/config-audit.json）
            candidates = [
                Path(__file__).resolve().parents[4] / "conf.yaml",
                Path("conf.yaml"),
            ]
        for _p in candidates:
            if _p.exists():
                _raw = yaml.safe_load(_p.read_text(encoding="utf-8")) or {}
                _family = (_raw.get("qq") or {}).get("family_qq") or []
                if _family:
                    return str(_family[0])
                break  # 配置读到了但 family_qq 为空 → 直接回退
    except Exception as e:
        logger.warning(f"[WS] 解析主人统一user_id失败，回退 desktop: {e}")
    return "desktop"


def _resolve_owner_name(
    owner_id: str,
    conf_path: Optional[Path] = None,
    profiles_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    2026-07-02 审计修复（批3）：称呼修复（用户明确要求：白不许叫用户"主人"）。

    解析对用户的称呼，三级回退：
      1. conf.yaml 的 qq.owner_name（显式配置最优先，当前配置里没有该键）
      2. data/memory/user_profiles/{owner_id}.json 的 user_name 字段
         （用户学习服务写入的画像；当前实测该文件存在且值为"小白"）
      3. 都没有 → 返回 None，调用方把 None 传给模型（让模型自然称呼），
         绝不再把"主人"硬编码进提示词

    Args:
        owner_id:     主人统一 user_id（来自 _resolve_owner_id()）
        conf_path:    显式指定 conf.yaml 路径（主要供测试注入）
        profiles_dir: 显式指定画像目录路径（主要供测试注入）

    Returns:
        解析出的称呼字符串（已 strip）；两级都取不到或解析失败时返回 None
    """
    # 第一级：conf.yaml 的 qq.owner_name
    try:
        import yaml

        if conf_path is not None:
            conf_candidates = [Path(conf_path)]
        else:
            # 2026-07-03 审计修复（批5）：项目根绝对路径提到首位，CWD 相对路径
            # 保留作兜底（同 _resolve_owner_id，依据 config-audit.json）
            conf_candidates = [
                Path(__file__).resolve().parents[4] / "conf.yaml",
                Path("conf.yaml"),
            ]
        for _p in conf_candidates:
            if _p.exists():
                _raw = yaml.safe_load(_p.read_text(encoding="utf-8")) or {}
                _name = (_raw.get("qq") or {}).get("owner_name")
                if _name is not None and str(_name).strip():
                    return str(_name).strip()
                break  # 配置读到了但没配 owner_name → 落到第二级
    except Exception as e:
        logger.warning(f"[WS] 读取 conf.yaml qq.owner_name 失败，尝试用户画像: {e}")

    # 第二级：用户画像 data/memory/user_profiles/{owner_id}.json 的 user_name
    try:
        if profiles_dir is not None:
            dir_candidates = [Path(profiles_dir)]
        else:
            dir_candidates = [
                Path("data/memory/user_profiles"),
                # 兜底：不依赖 CWD，按本文件位置定位项目根目录
                Path(__file__).resolve().parents[4] / "data" / "memory" / "user_profiles",
            ]
        for _d in dir_candidates:
            _f = _d / f"{owner_id}.json"
            if _f.exists():
                _profile = json.loads(_f.read_text(encoding="utf-8")) or {}
                _name = _profile.get("user_name")
                if _name is not None and str(_name).strip():
                    return str(_name).strip()
                break  # 画像存在但没有 user_name → 直接回退 None
    except Exception as e:
        logger.warning(f"[WS] 读取用户画像 user_name 失败: {e}")

    # 第三级：都没有 → None（让模型自然称呼）
    return None


async def handle_chat_websocket(
    websocket: WebSocket,
    agent: ChatAgent,
    tts: Optional[TTSInterface] = None,
    asr: Optional[ASRInterface] = None,
    vision: Optional[MultimodalVisionAdapter] = None,
    user_learning: Optional["UserLearningService"] = None,
) -> None:
    await websocket.accept()
    logger.info("WebSocket client connected")

    # 2026-07-02 审计修复（批2）：连接建立时解析主人统一user_id（qq.family_qq第一个号），
    # 桌面端从此与QQ端共用同一身份，立即继承QQ端已积累的好感度与画像
    owner_id = _resolve_owner_id()
    # 2026-07-02 审计修复（批3）：称呼不再硬编码"主人"，改为三级解析
    # （conf.yaml qq.owner_name → 画像 user_name → None 让模型自然称呼）
    owner_name: Optional[str] = _resolve_owner_name(owner_id)
    logger.info(
        f"[WS] 桌面端统一身份: user_id={owner_id}, "
        f"称呼={owner_name or '(未配置，交给模型自然称呼)'}"
    )

    # 2026-07-02 审计修复（批3）：连接级发送锁 —— 本连接上所有 websocket 发送
    # 串行化，防止后台回复任务与接收循环（或两轮回复交替时）的帧交错
    send_lock = asyncio.Lock()

    # 2026-07-02 审计修复（批3）：当前回复 = 取消令牌 + 后台任务（不再串行 await）。
    # 所有回复（用户 chat / 图片流程 / auto_chat / 跨平台桥）统一占用这一对引用，
    # interrupt / 新消息随时可取消。
    current_token: Optional[CancellationToken] = None
    current_task: Optional[asyncio.Task] = None

    def _reply_in_progress() -> bool:
        """2026-07-02 审计修复（批3）：是否确有一轮回复正在进行。"""
        return current_task is not None and not current_task.done()

    async def _cancel_current_reply() -> None:
        """
        2026-07-02 审计修复（批3）：取消进行中的回复。

        同时置位取消令牌 + cancel 后台任务，并 await 任务退出（吞 CancelledError），
        确保旧回复的 sentence/TTS 全部停止后才返回；最后释放两个引用
        （修复陈旧令牌：令牌不再在回复结束后继续存活误导冲突检测）。
        """
        nonlocal current_token, current_task
        if current_token is not None:
            current_token.cancel()
        task = current_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[WS] 回复任务取消收尾异常: {e}")
        current_task = None
        current_token = None

    async def _launch_reply(
        reply_coro_factory: Callable[[CancellationToken], Awaitable[None]],
    ) -> None:
        """
        2026-07-02 审计修复（批3）：取消进行中的回复并启动新的后台回复任务。

        接收循环随即返回继续 receive_text —— interrupt/新消息可随时打断。
        done/异常路径都会在 _runner 的 finally 里释放"当前任务"引用。

        Args:
            reply_coro_factory: 传入本轮取消令牌、返回回复协程的工厂函数
        """
        nonlocal current_token, current_task
        await _cancel_current_reply()
        token = CancellationToken()

        async def _runner() -> None:
            nonlocal current_token, current_task
            try:
                await reply_coro_factory(token)
            except asyncio.CancelledError:
                raise  # 被 _cancel_current_reply 取消：原样上抛由取消方吞掉
            except Exception as e:
                logger.error(f"[WS] Chat handler error: {e}")
                try:
                    await _send_error(websocket, f"处理失败: {str(e)[:100]}", send_lock)
                except Exception:
                    pass
            finally:
                # 只在引用仍指向本任务时清理（防止误清后启动的新任务）
                if current_task is asyncio.current_task():
                    current_task = None
                    current_token = None

        current_token = token
        current_task = asyncio.create_task(_runner())

    def _chat_reply_factory(
        text: str, *, is_user_message: bool,
    ) -> Callable[[CancellationToken], Awaitable[None]]:
        """构造一轮标准对话回复的协程工厂（按值捕获 text，防循环变量漂移）。"""
        def _factory(token: CancellationToken) -> Awaitable[None]:
            return _handle_chat_message(
                websocket, agent, tts, text, token,
                owner_id=owner_id, owner_name=owner_name,
                user_learning=user_learning, is_user_message=is_user_message,
                send_lock=send_lock,
            )
        return _factory

    # 从配置读取功能开关
    _ac_config = None
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        # 2026-07-03 审计修复（批5）：conf.yaml 改为从模块位置推导项目根的绝对
        # 路径，不再依赖 CWD（此前从其它工作目录启动会静默拿默认开关，
        # 依据 docs/audit-2026-07-02/config-audit.json）；本文件位于
        # src/white_salary/infrastructure/server/，项目根 = parents[4]
        _conf_path = _Path(__file__).resolve().parents[4] / "conf.yaml"
        if _conf_path.exists():
            _raw = _yaml.safe_load(_conf_path.read_text(encoding="utf-8")) or {}
            _ac_raw = _raw.get("auto_chat", {})
            from white_salary.core.auto_chat import AutoChatConfig
            _ac_config = AutoChatConfig(
                enabled=_ac_raw.get("enabled", True),
                morning_greeting=_ac_raw.get("morning_greeting", True),
                night_greeting=_ac_raw.get("night_greeting", True),
                care_reminder=_ac_raw.get("care_reminder", True),
                random_chat=_ac_raw.get("random_chat", True),
                daily_limit=_ac_raw.get("daily_limit", 3),
            )
    except Exception:
        pass

    # 启动主动聊天系统
    from white_salary.core.auto_chat import AutoChatManager

    async def _auto_chat_send(trigger_hint: str, *, ignore_quiet: bool = False) -> None:
        """
        主动聊天回调 — 触发原因交给主对话模型，由主模型自己组织语言。
        trigger_hint是提示（如"该问早安了"/"用户好久没说话了"），不是直接发给用户的话。

        2026-07-02 审计修复（批3）：主动回复纳入统一任务管理 ——
        - 与用户回复互斥：确有回复进行中时直接跳过本次主动发言（用户优先，
          新用户消息则会经 _launch_reply 直接取消进行中的 auto 回复）
        - 经 _launch_reply 占用 current_token/current_task，前端 interrupt 可取消

        2026-07-03 工具实现（批9）新增 ignore_quiet 参数：
        忙碌/静默模式期间跳过主动搭话（auto_chat 问候/关心/随机话题与桥播报都
        走本回调，检查点收敛在触发处，不动 AutoChatManager 内部）；
        提醒到点的通知传 ignore_quiet=True 穿透静默（用户明确设的提醒必须到人）。
        """
        if _should_skip_proactive(ignore_quiet):
            logger.info("[AutoChat] 忙碌/静默模式中，跳过本次主动发言")
            return
        if _reply_in_progress():
            logger.info("[AutoChat] 有回复正在进行，跳过本次主动发言")
            return
        # 构建主模型的输入
        chat_input = (
            f"[主动对话触发] {trigger_hint}\n"
            f"请你主动跟用户说点什么。用你自己的风格，自然一点，不要生硬。"
        )
        try:
            # 2026-07-02 审计修复（批2）：主动对话是系统构造的输入，
            # is_user_message=False → 不进画像学习（好感度本就只在chat分支计）
            await _launch_reply(_chat_reply_factory(chat_input, is_user_message=False))
        except Exception as e:
            logger.warning(f"[AutoChat] 主模型回复失败: {e}")

    auto_chat = AutoChatManager(send_callback=_auto_chat_send, config=_ac_config)
    await auto_chat.start()

    # 跨平台消息桥（QQ→桌面端推送）
    async def _bridge_check_loop():
        from white_salary.core.cross_platform import CrossPlatformBridge
        bridge = CrossPlatformBridge()
        while True:
            await asyncio.sleep(2)  # 每2秒检查
            try:
                # 2026-07-02 审计修复（批3）：有回复进行中先不取消息（留在桥队列，
                # 下一轮再取），避免桥回复抢占正在进行的用户回复
                if _reply_in_progress():
                    continue
                messages = bridge.pop_desktop_messages()
                # 2026-07-02 审计修复（批3）：同一轮取到的多条消息合并成一次提示，
                # 避免第一条占用回复槽位后其余消息被互斥逻辑丢弃
                # 2026-07-03 工具实现（批9）：桥消息分流——提醒类（source=reminder）
                # 穿透静默照常播报；普通播报静默期间丢弃（不留队列，避免静默结束后
                # 一次性轰炸；主人下静默命令即表示不要这些转发）
                # 2026-07-03 功能大项（批11）：passthrough=提醒+游戏事件（穿透静默）
                passthrough_parts, normal_parts = _partition_bridge_messages(messages)
                if normal_parts and _presence_is_quiet():
                    logger.info(
                        f"[Bridge] 忙碌/静默模式：丢弃{len(normal_parts)}条主动播报"
                    )
                    normal_parts = []
                parts: list[str] = passthrough_parts + normal_parts
                if parts:
                    hint = "；".join(parts) + "。请用语音回复用户。"
                    await _auto_chat_send(hint, ignore_quiet=bool(passthrough_parts))
            except Exception as e:
                # 2026-07-02 审计修复（批3）：桥轮询异常不再裸吞，至少留日志
                logger.warning(f"[Bridge] 桌面桥轮询异常: {e}")
    _bridge_task = asyncio.create_task(_bridge_check_loop())

    # per-connection实例（不共享，避免多客户端干扰）
    from white_salary.core.conflict_detector import ConflictDetector, ConflictType
    _conflict_detector = ConflictDetector()

    try:
        while True:
            raw_data = await websocket.receive_text()

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await _send_error(websocket, "Invalid JSON format", send_lock)
                continue

            msg_type = data.get("type", "")
            content = data.get("content", "")

            # 任何用户交互都通知AutoChat
            if msg_type in ("chat", "voice", "image"):
                auto_chat.notify_user_active()

            if msg_type == "chat":
                # 冲突检测（per-connection实例）
                conflict = _conflict_detector.check(content)

                # 话题追踪（2026-07-03 面板升级（批6）：开关关闭时为 None，判空跳过）
                if _topic_tracker is not None:
                    _topic_tracker.record_message(content, source="user")

                # 2026-07-02 审计修复（批2）：桌面端好感度开始累积。
                # 与 qq_handler.py:320-322 相同入口：每条真实用户消息计一次分。
                # 只在 chat 分支计（voice 会转成 chat 再回来、image/auto_chat
                # 是系统构造输入不计），因此不会重复计分。
                try:
                    from white_salary.core.affinity.manager import AffinityManager
                    _owner_aff = AffinityManager.get_for_user(owner_id)
                    _owner_aff.process_interaction()
                    _owner_aff.process_message(content)
                except Exception as _aff_e:
                    logger.warning(f"[WS] 桌面端好感度处理失败: {_aff_e}")

                # 2026-07-02 审计修复（批3）：冲突分支只在【确实有回复正在进行】时
                # 才走打断/撤回逻辑；否则含"等等/慢着"等触发词的消息按普通消息处理
                # （修复陈旧令牌导致正常消息被静默吞掉的问题）
                if conflict.has_conflict and _reply_in_progress():
                    if conflict.conflict_type == ConflictType.RETRACTION:
                        # 撤回/算了 → 取消当前回复，不处理
                        await _cancel_current_reply()
                        logger.info(f"[WS] 用户撤回: {content[:30]}")
                        await _send_json(websocket, {"type": "done", "content": ""}, send_lock)
                        continue
                    elif conflict.conflict_type == ConflictType.INTERRUPT:
                        # 打断 → 取消当前，等用户继续说
                        # 2026-07-02 审计修复（批3）：取消后回发一条 info 确认，
                        # 不再静默 continue 让用户以为消息被吞
                        await _cancel_current_reply()
                        logger.info(f"[WS] 用户打断: {content[:30]}")
                        await _send_json(websocket, {
                            "type": "info",
                            "content": "好，我先停下，你说。",
                        }, send_lock)
                        continue
                    elif conflict.conflict_type in (ConflictType.CORRECTION, ConflictType.SUPPLEMENT):
                        # 修正/补充 → 取消当前，把提示加到新消息里重新生成
                        await _cancel_current_reply()
                        content = f"{conflict.hint}\n用户说: {content}"
                        logger.info(f"[WS] 用户{conflict.conflict_type.value}: {content[:30]}")

                # 2026-07-02 审计修复（批3）：回复转入后台任务执行（_launch_reply
                # 内部先取消上一轮），接收循环立即回到 receive_text —— 长回复期间
                # interrupt/新消息不再排队，文件头宣称的"可打断"从此成立
                await _launch_reply(_chat_reply_factory(content, is_user_message=True))

            elif msg_type == "voice":
                if asr is None:
                    await _send_error(websocket, "语音识别未启用", send_lock)
                    continue

                try:
                    audio_bytes = base64.b64decode(content)

                    # 2026-07-02 审计修复（批2）：前端 MediaRecorder 产出的是 WebM/Opus，
                    # 此前硬标成 wav 上传导致 ASR 大面积 HTTP 500（语音输入从未可用）。
                    # 现在先按文件头探测真实格式：webm/ogg 先经 ffmpeg 转成 16kHz 单声道
                    # 真 WAV；转码不可用时把真实容器类型传给 ASR 适配器（按实际格式上传）。
                    detected = detect_audio_format(audio_bytes)
                    audio_format = detected if detected != "unknown" else "wav"
                    if detected in ("webm", "ogg"):
                        wav_bytes = await convert_to_wav(audio_bytes)
                        if wav_bytes:
                            audio_bytes = wav_bytes
                            audio_format = "wav"
                        else:
                            logger.warning(f"[ASR] ffmpeg转码不可用，按 {detected} 原格式上传")

                    audio = AudioData(samples=audio_bytes, sample_rate=16000, dtype=audio_format)
                    result = await asr.transcribe(audio)

                    if result.text.strip():
                        await _send_json(websocket, {
                            "type": "transcription",
                            "content": result.text,
                        }, send_lock)
                        logger.debug(f"[ASR] Transcribed: {result.text}")
                    else:
                        # 2026-07-02 审计修复（批2）：识别结果为空不再静默，
                        # 给前端info反馈（前端已有info分支处理）
                        await _send_json(websocket, {
                            "type": "info",
                            "content": "没听清，再说一次？",
                        }, send_lock)

                except Exception as e:
                    logger.warning(f"[ASR] Voice processing failed: {e}")
                    # 2026-07-02 审计修复（批2）：ASR失败不再静默，回传error给前端
                    await _send_error(websocket, f"语音识别失败：{str(e)[:100]}", send_lock)

            elif msg_type == "image":
                # 视觉流程：vision_llm识别图片 → 结果作为上下文 → 主对话模型用自己的话回复
                user_prompt = data.get("prompt", "看看这张图片")

                if vision is None:
                    # 没有视觉模型，告诉主模型
                    chat_input = f"[用户发了一张图片，但视觉系统未启用] 用户说: {user_prompt}"
                    # 2026-07-02 审计修复（批2）：图片流程的输入是系统构造的
                    # （拼了视觉识别结果），is_user_message=False → 不进画像学习
                    await _launch_reply(_chat_reply_factory(chat_input, is_user_message=False))
                    continue

                # 2026-07-02 审计修复（批3）：视觉识别+回复整体放进后台回复任务
                # （识别可能耗时数秒，期间接收循环仍可收 interrupt/新消息）
                async def _image_flow(
                    token: CancellationToken,
                    _img: str = content,
                    _prompt: str = user_prompt,
                ) -> None:
                    """图片流程：识别 → 拼上下文 → 交给主模型回复（占用回复槽位）。"""
                    try:
                        # Step 1: vision_llm获取图片描述（这只是原始数据）
                        description = await vision.describe_image(
                            _img, "详细描述这张图片中的内容和重点信息"
                        )
                        logger.debug(f"[Vision] Raw description: {description[:80]}")

                        # Step 2: 把视觉结果+用户的话一起交给主对话模型
                        if any(kw in description for kw in ["失败", "限流", "错误", "未配置", "太小"]):
                            chat_input = f"[用户让你看一张图片，但视觉识别失败了: {description}] 用户说: {_prompt}"
                        else:
                            chat_input = (
                                f"[视觉信息] 用户给你看了一张图片/截屏。"
                                f"视觉系统识别到的内容: {description}\n\n"
                                f"用户说: {_prompt}\n\n"
                                f"请根据看到的内容，用你自己的话回复用户。挑重点说，像人一样自然地描述，不要复述原文。"
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"[Vision] Processing failed: {e}")
                        # 失败也交给主模型说
                        chat_input = f"[用户让你看图片，但出错了: {e}] 用户说: {_prompt}"

                    if token.cancelled:
                        return
                    # Step 3: 走正常对话流程（主模型回复）
                    # 2026-07-02 审计修复（批2）：图片流程的输入是系统构造的
                    # （拼了视觉识别结果），is_user_message=False → 不进画像学习
                    await _handle_chat_message(
                        websocket, agent, tts, chat_input, token,
                        owner_id=owner_id, owner_name=owner_name,
                        user_learning=user_learning, is_user_message=False,
                        send_lock=send_lock,
                    )

                await _launch_reply(_image_flow)

            elif msg_type == "interrupt":
                # 2026-07-02 审计修复（批3）：回复在后台任务里跑，本分支随时可达，
                # interrupt 现在真的能打断进行中的回复（含 auto_chat/桥的主动播报）
                if _reply_in_progress() or current_token is not None:
                    await _cancel_current_reply()
                    logger.info("[WS] Interrupted by user")

            elif msg_type == "reset":
                # 2026-07-02 审计修复（批3）：重置前先取消进行中的回复任务
                await _cancel_current_reply()
                agent.reset_conversation()
                await _send_json(websocket, {"type": "info", "content": "Conversation reset"}, send_lock)

            else:
                await _send_error(websocket, f"Unknown message type: {msg_type}", send_lock)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except asyncio.CancelledError:
        logger.info("WebSocket task cancelled")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await _send_error(websocket, str(e), send_lock)
        except Exception:
            pass
    finally:
        # 2026-07-02 审计修复（批3）：断开时取消进行中的后台回复任务
        # （其内部 finally 会对 LLM 异步生成器 aclose、回收 TTS worker）
        try:
            await _cancel_current_reply()
        except Exception:
            pass
        # 清理跨平台桥检查任务
        if '_bridge_task' in dir() and _bridge_task:
            _bridge_task.cancel()
            try:
                await _bridge_task
            except (asyncio.CancelledError, Exception):
                pass
        await auto_chat.stop()


async def _emit_plugin_reply(
    websocket: WebSocket,
    agent: ChatAgent,
    tts: Optional[TTSInterface],
    reply_text: str,
    cancel: CancellationToken,
    owner_id: str = "desktop",
    owner_name: Optional[str] = None,
    user_input: str = "",
    send_lock: Optional[asyncio.Lock] = None,
) -> None:
    """
    2026-07-03 功能大项（批11）：把插件抢答的回复按标准帧协议一次性下发。

    插件回复不是流式的（一次拿到整段），这里补齐前端期望的帧序列：
    reply_start(source=user) → sentence(index=0) → [sentence_audio] → done，
    并写入跨平台对话日志（与 LLM 回复一致，保证桌面端能显示+朗读+留痕）。
    经 process_reply 让 on_reply 型插件也有机会改写抢答内容。
    全程 try/except 兜底：出错只记日志，不影响 WebSocket 连接。

    Args:
        websocket:   连接
        agent:       当前 ChatAgent（取情绪语速倍率用）
        tts:         TTS 适配器（None 则只发文本不合成语音）
        reply_text:  插件 on_message 返回的回复原文
        cancel:      取消令牌（取消则静默返回）
        owner_id:    主人统一 user_id（写对话日志用）
        owner_name:  对用户的称呼（写对话日志用，可为 None）
        user_input:  本轮用户原始输入（写对话日志用）
        send_lock:   连接级发送锁
    """
    try:
        # on_reply 型插件仍有机会改写抢答内容
        _plugin_mgr = _get_plugin_manager()
        if _plugin_mgr is not None:
            try:
                reply_text = await _plugin_mgr.process_reply(reply_text)
            except Exception as _pre:
                logger.warning(f"[WS] 插件 on_reply 改写抢答内容异常，保留原文: {_pre}")

        clean_text = strip_xml_tags(reply_text).strip()
        if not clean_text or cancel.cancelled:
            return

        await _send_json(websocket, {
            "type": "reply_start", "source": "user",
        }, send_lock)
        await _send_json(websocket, {
            "type": "sentence", "content": clean_text, "index": 0,
        }, send_lock)

        # TTS 合成（可选；失败只跳过语音，不影响文本）
        if tts is not None and not cancel.cancelled:
            try:
                tts_text = strip_action_tags(clean_text)
                if tts_text and is_valid_for_tts(tts_text):
                    if hasattr(tts, "synthesize_with_speed"):
                        audio = await tts.synthesize_with_speed(  # type: ignore[union-attr]
                            tts_text,
                            speed_multiplier=_get_emotion_speed_multiplier(agent),
                        )
                    else:
                        audio = await tts.synthesize(tts_text)  # type: ignore[union-attr]
                    if audio.samples and len(audio.samples) > 0 and not cancel.cancelled:
                        audio_b64 = base64.b64encode(audio.samples).decode("ascii")
                        await _send_json(websocket, {
                            "type": "sentence_audio",
                            "content": audio_b64,
                            "format": audio.dtype,
                            "index": 0,
                        }, send_lock)
            except Exception as _te:
                logger.warning(f"[WS] 插件回复 TTS 合成跳过: {_te}")

        if cancel.cancelled:
            return
        await _send_json(websocket, {"type": "done", "content": clean_text}, send_lock)

        # 写入跨平台对话日志（与 LLM 回复口径一致）
        try:
            from white_salary.core.memory.conversation_log import ConversationLog
            conv_log = ConversationLog.get_instance()
            conv_log.record(
                platform="desktop",
                user_name=owner_name or "",
                user_id=owner_id,
                group_id="",
                user_msg=user_input,
                ai_reply=clean_text,
            )
        except Exception as _le:
            logger.warning(f"[WS] 插件抢答对话日志写入失败: {_le}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"[WS] 下发插件抢答回复失败: {e}")


async def _handle_chat_message(
    websocket: WebSocket,
    agent: ChatAgent,
    tts: Optional[TTSInterface],
    user_input: str,
    cancel: CancellationToken,
    owner_id: str = "desktop",
    owner_name: Optional[str] = None,
    user_learning: Optional["UserLearningService"] = None,
    is_user_message: bool = True,
    send_lock: Optional[asyncio.Lock] = None,
) -> None:
    """
    处理一轮对话：LLM流式生成 → 【边生成边】逐句下发文本+TTS音频 → 收尾记录。

    2026-07-02 审计修复（批2）新增参数：
        owner_id:        主人统一user_id（来自 _resolve_owner_id()，与QQ端共账）
        user_learning:   run_server.py 装配的用户学习服务实例（替代原先
                         调用不存在的 UserLearningService.get_instance() 的死代码）
        is_user_message: 是否真实用户消息；auto_chat/图片等系统构造输入为False，
                         不进画像学习，避免污染用户画像

    2026-07-02 审计修复（批3）改造点：
        - 真流式：切句在 chunk 接收循环内增量进行（_drain_sentences），凑齐一句
          立即发 sentence；TTS 由后台 worker 按句序合成并发 sentence_audio
        - 每轮回复开始、第一条 sentence 之前先发 reply_start
          （source: 真实用户消息=user / auto_chat/桥/图片等系统触发=auto）
        - 所有退出路径（正常/取消/超时/异常）对 LLM 异步生成器调用 aclose()
        - 120秒超时分支追加 buffer 后立即清空，修"超时内容播两遍"
        - owner_name 改为 Optional[str]：不再硬编码"主人"；None 时把 None 传给
          模型让其自然称呼，对话日志/用户学习则记空串
        - send_lock: 连接级发送锁，本轮所有 websocket 发送串行化（防帧交错）
    """
    if not user_input.strip():
        await _send_error(websocket, "Message cannot be empty", send_lock)
        return

    logger.debug(f"User message: {user_input[:50]}...")

    # ========== 插件消息钩子（抢答）==========
    # 2026-07-03 功能大项（批11）：LLM 生成前先让插件处理消息。
    # 只对真实用户消息走抢答（is_user_message=True）；auto_chat/图片流程等
    # 系统构造输入不触发插件（它们不是"用户说的话"）。on_message 型插件返回
    # 非空 → 直接把它作为完整回复下发（reply_start+sentence+done+TTS），
    # 跳过整段 LLM 流式。process_message 内部有超时+异常兜底，这里再包一层：
    # 出任何岔子都当作"无插件拦截"继续走正常 LLM 流程。
    if is_user_message and not cancel.cancelled:
        _plugin_mgr = _get_plugin_manager()
        if _plugin_mgr is not None:
            try:
                _plugin_reply = await _plugin_mgr.process_message(user_input, owner_id)
            except Exception as _pe:
                logger.warning(f"[WS] 插件 on_message 钩子异常，走正常LLM流程: {_pe}")
                _plugin_reply = None
            if _plugin_reply and not cancel.cancelled:
                logger.info(f"[WS] 插件抢答: {_plugin_reply[:30]}")
                await _emit_plugin_reply(
                    websocket, agent, tts, _plugin_reply, cancel,
                    owner_id=owner_id, owner_name=owner_name,
                    user_input=user_input, send_lock=send_lock,
                )
                return

    # 2026-07-02 审计修复（批3）：TTS 后台 worker —— 句子文本入队，worker 按
    # 句序合成并下发 sentence_audio；这样 TTS 合成不阻塞 LLM chunk 接收循环
    tts_queue: "asyncio.Queue[Optional[tuple[int, str]]]" = asyncio.Queue()
    tts_worker: Optional[asyncio.Task] = None

    async def _tts_worker_loop() -> None:
        """按句序逐条合成 TTS 并下发 sentence_audio（None 哨兵 = 收工退出）。"""
        while True:
            item = await tts_queue.get()
            if item is None:
                break
            idx, sentence = item
            if cancel.cancelled:
                continue  # 已取消：只清空队列，不再合成
            try:
                tts_text = strip_action_tags(sentence)
                if tts_text and is_valid_for_tts(tts_text):
                    # 2026-07-03 面板升级（批6）：情绪语速接通——本地 GPT-SoVITS
                    # 适配器支持按调用传语速倍率（配置基准语速×情绪倍率在适配器内
                    # 相乘）；SiliconFlow 兜底等无该方法的适配器不支持按调用调速，
                    # 跳过情绪调速走原合成路径（行为不变）
                    if hasattr(tts, "synthesize_with_speed"):
                        audio = await tts.synthesize_with_speed(  # type: ignore[union-attr]
                            tts_text,
                            speed_multiplier=_get_emotion_speed_multiplier(agent),
                        )
                    else:
                        audio = await tts.synthesize(tts_text)  # type: ignore[union-attr]
                    if audio.samples and len(audio.samples) > 0 and not cancel.cancelled:
                        audio_b64 = base64.b64encode(audio.samples).decode("ascii")
                        await _send_json(websocket, {
                            "type": "sentence_audio",
                            "content": audio_b64,
                            "format": audio.dtype,
                            "index": idx,
                        }, send_lock)
                        logger.debug(
                            f"[TTS] Sentence {idx}: {len(tts_text)} chars -> "
                            f"{len(audio.samples)} bytes"
                        )
            except Exception as e:
                logger.warning(f"[TTS] Sentence {idx} skipped: {e}")

    try:
        # 2026-07-02 审计修复（批3）：reply_start 协议 —— 每轮回复开始、第一条
        # sentence 之前通知前端（前端据此停掉上一轮音频、清空句子队列、游标归0）。
        # source: 真实用户消息=user；auto_chat/跨平台桥/图片流程等系统触发=auto
        await _send_json(websocket, {
            "type": "reply_start",
            "source": "user" if is_user_message else "auto",
        }, send_lock)

        sentences: list[str] = []   # 已下发的完整句子（收尾拼 full_reply 用）
        buffer = ""                 # 尚未凑满一句的残留
        sent_idx = 0                # 已下发 sentence 帧的 index
        emotion_sent = False        # emotion 帧每轮只发第一次

        if tts is not None:
            tts_worker = asyncio.create_task(_tts_worker_loop())

        async def _emit_sentence(sentence: str) -> None:
            """
            2026-07-02 审计修复（批3）：凑齐一句立即下发。

            发 sentence 帧（流式模式下逐句提取情绪标签，首个情绪即发 emotion 帧），
            并把句子排入 TTS 队列由 worker 异步合成 sentence_audio。
            """
            nonlocal sent_idx, emotion_sent
            clean_sentence, emos = extract_emotion_tags(sentence)
            if emos and not emotion_sent and not cancel.cancelled:
                await _send_json(websocket, {"type": "emotion", "content": emos[0]}, send_lock)
                emotion_sent = True

            display_text = strip_xml_tags(clean_sentence or sentence)
            if not display_text:
                return

            # Content filter on each sentence before sending
            filter_result = _content_filter.filter(display_text)
            display_text = filter_result.text

            idx = sent_idx
            sent_idx += 1
            await _send_json(websocket, {
                "type": "sentence",
                "content": display_text,
                "index": idx,
            }, send_lock)
            if tts_worker is not None:
                tts_queue.put_nowait((idx, clean_sentence or sentence))

        # 2026-07-02 审计修复（批2）：传统一身份，ChatAgent内部的
        # 好感度提示注入/记忆提取从此用主人统一user_id（不再是"desktop"）
        # 2026-07-02 审计修复（批3）：user_name 传解析结果（可能为 None，
        # 由模型自然称呼），不再硬编码"主人"
        stream_gen = agent.chat_stream_with_tools(
            user_input, user_name=owner_name, user_id=owner_id,
        )
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream_gen.__anext__(), timeout=_LLM_STREAM_TIMEOUT,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.warning(f"[WS] LLM流式回复超时({int(_LLM_STREAM_TIMEOUT)}秒)")
                    # 2026-07-02 审计修复（批3）：追加残留后必须清空 buffer，
                    # 防止循环外收尾把同一内容再追加一遍（修"超时内容播两遍"）
                    if buffer.strip():
                        tail = buffer.strip()
                        sentences.append(tail)
                        await _emit_sentence(tail)
                        buffer = ""
                    if not sentences:
                        fallback = "...抱歉，我刚才走神了。你说什么？"
                        sentences.append(fallback)
                        await _emit_sentence(fallback)
                    break
                if cancel.cancelled:
                    logger.debug("[WS] LLM streaming cancelled")
                    break
                buffer += chunk
                # 2026-07-02 审计修复（批3）：真流式核心 —— 每收到一个 chunk 就
                # 增量切句，凑齐一句立即下发（不再等整个 LLM 流结束才切句）
                new_sentences, buffer = _drain_sentences(buffer)
                for s in new_sentences:
                    sentences.append(s)
                    await _emit_sentence(s)
        finally:
            # 2026-07-02 审计修复（批3）：所有退出路径（正常/用户取消/超时/异常）
            # 都显式关闭 LLM 异步生成器，释放上游 HTTP 流
            try:
                await stream_gen.aclose()
            except (asyncio.CancelledError, Exception):
                pass

        if cancel.cancelled:
            return

        # 流正常结束：把最后的半句残留作为收尾句下发
        if buffer.strip():
            tail = buffer.strip()
            sentences.append(tail)
            await _emit_sentence(tail)
            buffer = ""

        if not sentences:
            fallback = "...抱歉，我刚才走神了。你说什么？"
            sentences.append(fallback)
            await _emit_sentence(fallback)

        # 等待 TTS worker 把队列里的句子全部合成完（保证 done 帧最后到达）
        if tts_worker is not None:
            tts_queue.put_nowait(None)
            await tts_worker

        full_reply = "".join(sentences)
        clean_text, _emos = extract_emotion_tags(full_reply)
        final_text = strip_xml_tags(clean_text or full_reply)

        # 机器话过滤（保持原行为：只作用于 done 正文与对话日志）
        try:
            from white_salary.core.memory.human_like_filter import HumanLikeFilter
            if not hasattr(_handle_chat_message, '_human_filter'):
                _handle_chat_message._human_filter = HumanLikeFilter()
            final_text = _handle_chat_message._human_filter.filter_response(final_text)
        except Exception:
            pass

        # ========== 插件回复钩子（改写）==========
        # 2026-07-03 功能大项（批11）：done 正文与对话日志写入前让 on_reply 型
        # 插件改写最终回复（sentence 已流式发出，这里改写的是 done 正文/留痕文本，
        # 与 QQ 端 process_reply 口径一致）。process_reply 内部有 SafeExecutor+顶层
        # 兜底，这里再包一层：出任何岔子都保留过滤后的原文。
        _reply_plugin_mgr = _get_plugin_manager()
        if _reply_plugin_mgr is not None:
            try:
                final_text = await _reply_plugin_mgr.process_reply(final_text)
            except Exception as _pre:
                logger.warning(f"[WS] 插件 on_reply 钩子异常，保留原回复: {_pre}")

        # Step 3: Send expression command (if emotion system available)
        if not cancel.cancelled and hasattr(agent, '_memory_manager') and agent._memory_manager:
            try:
                emo_tracker = getattr(agent._memory_manager, '_emotion_tracker', None)
                if not emo_tracker:
                    emo_tracker = getattr(agent._memory_manager, 'emotion', None)
                if emo_tracker and hasattr(emo_tracker, 'get_expression_command'):
                    expr_cmd = emo_tracker.get_expression_command()
                    await _send_json(websocket, {
                        "type": "expression",
                        "content": expr_cmd,
                    }, send_lock)
            except Exception:
                pass

        # Step 4: Send completion + record to conversation log
        if not cancel.cancelled:
            await _send_json(websocket, {"type": "done", "content": final_text}, send_lock)
            logger.debug(f"Reply complete ({sent_idx} sentences): {final_text[:50]}...")

            # 写入跨平台对话日志 + 触发用户学习
            try:
                from white_salary.core.memory.conversation_log import ConversationLog
                conv_log = ConversationLog.get_instance()
                # 2026-07-02 审计修复（批2）：user_id改用主人统一id（与QQ端同账），
                # platform维持"desktop"不变，按user_id聚合时两端视为同一人
                # 2026-07-02 审计修复（批3）：user_name 用解析出的称呼或空串，
                # 不再写死"主人"
                conv_log.record(
                    platform="desktop",
                    user_name=owner_name or "",
                    user_id=owner_id,
                    group_id="",
                    user_msg=user_input,
                    ai_reply=final_text,
                )
                # 2026-07-02 审计修复（批2）：原代码调用不存在的
                # UserLearningService.get_instance()（hasattr恒False，画像学习
                # 从未运行）。现改用run_server.py装配时传入的实例；只对真实
                # 用户消息学习（on_message内部会自动按阈值触发后台learn）
                try:
                    if user_learning is not None and is_user_message:
                        user_learning.on_message(owner_id, owner_name or "", user_input)
                except Exception as _ul_e:
                    logger.warning(f"[WS] 桌面端用户学习失败: {_ul_e}")
            except Exception as _log_e:
                # 2026-07-02 审计修复（批2）：不再裸吞异常，至少留日志
                logger.warning(f"[WS] 对话日志写入失败: {_log_e}")

    except asyncio.CancelledError:
        # 2026-07-02 审计修复（批3）：任务被上层（_cancel_current_reply）取消：
        # 原样上抛，由取消方 await 并吞掉；本函数的 finally 负责清理 TTS worker
        raise
    except Exception as e:
        if not cancel.cancelled:
            error_msg = str(e)
            logger.error(f"Reply generation failed: {error_msg[:100]}")

            # Auto-reset on 400 sensitive content error
            if "400" in error_msg and ("敏感" in error_msg or "sensitive" in error_msg.lower()):
                agent.reset_conversation()
                await _send_json(websocket, {
                    "type": "info",
                    "content": "对话已自动重置（供应商内容过滤触发）",
                }, send_lock)
                logger.warning("[WS] Auto-reset due to content filter 400")
            else:
                await _send_error(websocket, f"回复生成失败: {error_msg[:100]}", send_lock)
    finally:
        # 2026-07-02 审计修复（批3）：TTS worker 兜底回收 —— 正常路径此前已
        # await 其退出（done 状态，跳过）；取消/异常路径直接 cancel 并吞异常
        if tts_worker is not None and not tts_worker.done():
            tts_worker.cancel()
            try:
                await tts_worker
            except (asyncio.CancelledError, Exception):
                pass


async def _send_json(
    websocket: WebSocket, data: dict, lock: Optional[asyncio.Lock] = None,
) -> None:
    """
    发送一条 JSON 帧。

    2026-07-02 审计修复（批3）：新增可选 lock 参数 —— 同一连接上的所有发送
    共用一把 asyncio.Lock，防止后台回复任务与接收循环的帧交错。
    """
    payload = json.dumps(data, ensure_ascii=False)
    if lock is not None:
        async with lock:
            await websocket.send_text(payload)
    else:
        await websocket.send_text(payload)


async def _send_error(
    websocket: WebSocket, message: str, lock: Optional[asyncio.Lock] = None,
) -> None:
    """发送一条 error 帧（2026-07-02 审计修复（批3）：透传发送锁）。"""
    await _send_json(websocket, {"type": "error", "content": message}, lock)
