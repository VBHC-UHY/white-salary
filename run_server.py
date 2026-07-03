"""
White Salary - 主程序入口

运行这个文件就能启动 White Salary 后端服务。
用法：python run_server.py [--debug] [--port 12400]

启动后：
  - HTTP API: http://localhost:12400
  - WebSocket: ws://localhost:12400/ws/chat
  - 健康检查: http://localhost:12400/health

前端（Electron桌面应用）通过 WebSocket 连接到这个服务。
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn
from loguru import logger


def kill_port(port: int) -> None:
    """强制释放指定端口 — 杀掉占用该端口的所有进程（Windows专用）。"""
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["netstat", "-aon"],
            capture_output=True, text=True, timeout=5,
        )
        pids_to_kill = set()
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    try:
                        pids_to_kill.add(int(parts[-1]))
                    except ValueError:
                        pass
        my_pid = os.getpid()
        for pid in pids_to_kill:
            if pid == my_pid or pid == 0:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"已杀掉占用端口 {port} 的进程 (PID: {pid})")
            except (OSError, PermissionError):
                # 兜底：用taskkill强杀
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=5,
                )
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    返回:
        解析后的参数对象，包含 debug、port、host 等字段
    """
    parser = argparse.ArgumentParser(
        description="White Salary - 超强AI智能体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="开启调试模式（输出更详细的日志）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="服务端口号（默认从配置文件读取，默认12400）",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="监听地址（默认从配置文件读取，默认localhost）",
    )
    return parser.parse_args()


def setup_logging(debug: bool = False) -> None:
    """
    配置日志系统。

    参数:
        debug: 是否开启调试模式（True=输出DEBUG级别日志）
    """
    # 移除loguru默认的控制台输出，重新配置
    logger.remove()

    # 控制台日志：带颜色、带时间
    log_level = "DEBUG" if debug else "INFO"
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # 文件日志：记录所有级别，按天轮转，保留30天
    logger.add(
        "logs/white_salary_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
    )


def main() -> None:
    """
    主函数：解析参数 → 配置日志 → 加载配置 → 创建组件 → 启动服务。
    """
    # 第一步：解析命令行参数
    args = parse_args()

    # 第二步：配置日志
    setup_logging(debug=args.debug)

    logger.info("=" * 60)
    logger.info("  White Salary 正在启动...")
    logger.info("=" * 60)

    # 第三步：加载配置
    from white_salary.infrastructure.config import load_config

    project_root = Path(__file__).parent
    config = load_config(project_root=project_root)

    # 命令行参数覆盖配置文件
    host = args.host or config.server.host
    port = args.port or config.server.port

    # 自动清理端口占用（防止重启时端口冲突）
    kill_port(port)

    # 第四步：创建核心组件
    from white_salary.adapters.llm.openai_compatible import OpenAICompatibleAdapter
    from white_salary.adapters.llm.factory import PRESET_PROVIDERS
    from white_salary.core.agent.chat_agent import ChatAgent
    from white_salary.core.memory.short_term import ShortTermMemory
    from white_salary.core.personality.character import PersonalityManager

    # 创建LLM适配器
    provider = config.llm.provider.lower()
    api_key = config.llm.api_key
    base_url = config.llm.base_url or PRESET_PROVIDERS.get(provider, {}).get("base_url", "")
    model = config.llm.model or PRESET_PROVIDERS.get(provider, {}).get("default_model", "")

    if not api_key:
        logger.warning("LLM API密钥未配置！请在控制面板(Ctrl+,)中设置API Key")

    llm = OpenAICompatibleAdapter(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    personality = PersonalityManager(
        character_name=config.personality.character_name,
        system_prompt_file=config.personality.system_prompt_file,
        project_root=project_root,
    )

    memory = ShortTermMemory(
        max_turns=config.memory.short_term_max_turns,
        persist_path=str(project_root / "data" / "chat_history" / "current.json"),
    )

    from white_salary.core.memory.manager import MemoryManager
    # AffinityManager不再全局创建，由各模块按用户动态获取
    from white_salary.adapters.tools.registry import ToolRegistry

    # 创建多角色LLM适配器（每个角色使用独立配置）
    # 2026-07-03 审计修复（批5）：删除 yaml.safe_load 裸读 conf.yaml 的旁路缓存
    # （_cached_conf），改为统一消费 load_config() 的结果——conf.default.yaml 与
    # conf.yaml 深合并且经 Pydantic 校验，消除"双轨配置"割裂
    # （依据 docs/audit-2026-07-02/config-audit.json）。
    from white_salary.infrastructure.config.models import RoleLLMConfig

    def _create_role_llm(role_key: str) -> "OpenAICompatibleAdapter | None":
        """从合并后的配置（config.llm_xxx）读取指定角色的LLM配置，创建适配器。"""
        try:
            role_conf: RoleLLMConfig = getattr(config, role_key)
            if role_conf.api_key and role_conf.model:
                role_base = role_conf.base_url or PRESET_PROVIDERS.get(
                    role_conf.provider, {}
                ).get("base_url", "")
                adapter = OpenAICompatibleAdapter(
                    api_key=role_conf.api_key,
                    base_url=role_base,
                    model=role_conf.model,
                )
                logger.info(f"  {role_key}: {role_conf.provider or '?'} / {role_conf.model}")
                return adapter
        except Exception as e:
            # 2026-07-03 审计修复（批5）：日志级别 debug→warning——此前角色通道
            # 加载失败只记 debug，llm_memory 挂掉曾静默近一个月无人察觉
            logger.warning(f"  {role_key}: 未配置或加载失败 ({e})")
        return None

    logger.info("Multi-role LLM (10-channel):")
    tool_llm = _create_role_llm("llm_tool")
    memory_llm = _create_role_llm("llm_memory")
    # 2026-07-03 面板升级（批6）：emotion.enabled 接活——关闭时不创建情感分析
    # 通道（MemoryManager 现有门槛逻辑：emotion_llm 为 None 时第8步 LLM 情感
    # 分析自动跳过），修复该开关零消费方的问题；默认 enabled=true=原行为
    if config.emotion.enabled:
        emotion_llm = _create_role_llm("llm_emotion")
    else:
        emotion_llm = None
        logger.info("  llm_emotion: 已按 emotion.enabled=false 跳过（LLM情感分析关闭）")
    vision_llm_adapter = _create_role_llm("llm_vision")
    postprocess_llm = _create_role_llm("llm_postprocess")
    detect_llm = _create_role_llm("llm_detect")
    background_llm = _create_role_llm("llm_background")

    # Log which roles are active
    roles = {
        "tool": tool_llm, "memory": memory_llm, "emotion": emotion_llm,
        "vision": vision_llm_adapter, "postprocess": postprocess_llm,
        "detect": detect_llm, "background": background_llm,
    }
    active = [k for k, v in roles.items() if v]
    logger.info(f"  Active: {', '.join(active) if active else 'main only'} ({len(active)}/7 roles)")

    # 2026-07-03 面板升级（批6）：长期记忆引擎开关接活——把
    # config.memory.long_term_provider 注入 long_term_store 的进程级默认值
    # （必须早于 MemoryManager 创建；'none'=跳过Chroma只用关键词检索），
    # 修复"对话设置"页 mem-provider 下拉零消费方的问题
    from white_salary.core.memory.long_term_store import set_default_long_term_provider
    set_default_long_term_provider(config.memory.long_term_provider)
    logger.info(f"[Memory] 长期记忆引擎: {config.memory.long_term_provider}")

    memory_manager = MemoryManager(
        data_dir=str(project_root / "data" / "memory"),
        memory_llm=memory_llm,
        emotion_llm=emotion_llm,  # 情感分析用独立的emotion_llm，不抢主模型资源
    )
    # 好感度不再使用全局实例，改为ChatAgent内部按用户动态获取
    # AffinityManager.get_for_user(user_id) 在需要时自动创建

    # 初始化跨平台对话日志（桌面+QQ共用）
    from white_salary.core.memory.conversation_log import ConversationLog
    conv_log = ConversationLog.get_instance(data_dir=str(project_root / "data" / "memory"))
    logger.info(f"[ConvLog] 跨平台对话日志: {conv_log.total_count} 条历史记录")

    # 初始化用户学习服务（自动学习用户特征和偏好）
    # 2026-07-03 面板升级（批6）：features.user_learning 接活——关闭时传 None
    # （下游 websocket_handler / qq_handler 本就判 None 跳过），修复该开关
    # 零消费方的问题；默认 true=原行为
    user_learning = None
    if config.features.user_learning:
        from white_salary.core.services.user_learning import UserLearningService
        user_learning = UserLearningService(
            memory_manager=memory_manager,
            learning_llm=memory_llm,  # 用memory_llm做分析，不占主模型
            data_dir=str(project_root / "data" / "memory"),
        )
        logger.info(f"[UserLearn] 用户学习服务就绪 (画像: {'有' if user_learning.get_profile() else '无'})")
    else:
        logger.info("[UserLearn] 已按 features.user_learning=false 关闭（画像学习跳过）")

    # 初始化记忆整理服务（每日自动去重+清理过期）
    from white_salary.core.services.memory_consolidation import MemoryConsolidationService
    mem_consolidation = MemoryConsolidationService(
        memory_manager=memory_manager,
        consolidation_llm=memory_llm,
    )

    tool_registry = ToolRegistry()

    # 插件系统加载
    try:
        from white_salary.core.plugins.manager import PluginManager
        plugin_manager = PluginManager(plugins_dir="plugins")
        plugin_manager.discover()
        import asyncio
        asyncio.get_event_loop().run_until_complete(plugin_manager.load_all())
        plugin_manager.register_tools_to_registry(tool_registry)
        logger.info(f"[Startup] 插件系统: {plugin_manager.count}个插件已加载")
    except Exception as e:
        logger.warning(f"[Startup] 插件系统加载失败: {e}")
        plugin_manager = None

    agent = ChatAgent(
        llm=llm,
        personality=personality,
        memory=memory,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        tool_llm=tool_llm,  # 工具判断用独立的tool_llm
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        # 2026-07-03 面板升级（批6）：内容过滤开关接活（原ChatAgent内部硬编码True）
        content_filter_enabled=config.features.content_filter,
    )

    logger.info(f"LLM: {provider} / {model}")
    logger.info(f"Character: {config.personality.character_name}")

    # Step 5: Create TTS adapter (local GPT-SoVITS primary, SiliconFlow fallback)
    # 2026-07-03 审计修复（批5）：TTS 全部参数配置化——tts 节此前是"写了没人读"的
    # 死配置（真实行为硬编码在这里），现改为读 config.tts.*，各默认值 = 原硬编码值，
    # 行为不变，但用户在控制面板改 TTS 从此真实生效
    # （依据 docs/audit-2026-07-02/config-audit.json）。
    from white_salary.adapters.tts.gpt_sovits_adapter import GPTSoVITSAdapter
    from white_salary.adapters.tts.siliconflow_adapter import SiliconFlowTTSAdapter
    from urllib.parse import urlparse
    import socket

    tts_adapter = None
    # 2026-07-02 审计修复（批3）：参考音频改为项目内绝对路径（assets/tts/），
    # 不再依赖 GPT-SoVITS 进程的 cwd 去解析寄生在其训练日志目录里的相对路径。
    # 2026-07-03 审计修复（批5）：路径来自 config.tts.ref_audio（相对路径按项目根
    # 解析），环境变量 WS_TTS_REF_AUDIO / WS_TTS_REF_TEXT 仍最优先（保持旧行为）
    _ref_audio_cfg = Path(config.tts.ref_audio)
    if not _ref_audio_cfg.is_absolute():
        _ref_audio_cfg = project_root / _ref_audio_cfg
    ref_audio = os.environ.get("WS_TTS_REF_AUDIO", str(_ref_audio_cfg))
    ref_text = os.environ.get("WS_TTS_REF_TEXT", config.tts.ref_text)

    # Check if local GPT-SoVITS is running（地址来自 config.tts.local_api_url）
    _tts_url = urlparse(config.tts.local_api_url)
    _tts_host: str = _tts_url.hostname or "127.0.0.1"
    _tts_port: int = _tts_url.port or 9880
    local_tts_available = False
    try:
        s = socket.create_connection((_tts_host, _tts_port), timeout=2)
        s.close()
        local_tts_available = True
    except (ConnectionRefusedError, OSError, socket.timeout):
        pass

    if local_tts_available:
        tts_adapter = GPTSoVITSAdapter(
            api_url=config.tts.local_api_url,
            ref_audio_path=ref_audio,
            ref_text=ref_text,
            ref_lang="zh",
            # 2026-07-03 面板升级（批6）：基准语速接配置（默认1.0=原构造默认值，
            # 行为不变）；合成时适配器内部会再乘上情绪语速倍率
            speed=config.tts.speed,
        )
        logger.info(f"TTS: Local GPT-SoVITS (white_salary_v1, speed={config.tts.speed})")
    else:
        logger.warning("Local GPT-SoVITS not available, falling back to SiliconFlow")
        try:
            # 2026-07-03 审计修复（批5）：兜底密钥优先用 config.tts.fallback_api_key；
            # 留空时保留原有"扫角色LLM配置找SiliconFlow密钥"的兜底逻辑（行为不变）
            _tts_key: str = config.tts.fallback_api_key
            if not _tts_key:
                for _r in ("llm_vision", "llm_postprocess", "llm_memory"):
                    _rc: RoleLLMConfig = getattr(config, _r)
                    if "siliconflow" in _rc.provider.lower() and _rc.api_key:
                        _tts_key = _rc.api_key
                        break
            # 2026-07-03 面板升级（批6）：SiliconFlow 兜底适配器不支持语速参数
            # （无 speed 构造参数、无 synthesize_with_speed 方法），config.tts.speed
            # 与情绪调速对兜底链路跳过——websocket_handler 合成处按方法存在性判断
            tts_adapter = SiliconFlowTTSAdapter(
                api_key=_tts_key,
                model=config.tts.fallback_model,
                voice=config.tts.fallback_voice,
            )
            logger.info("TTS: SiliconFlow CosyVoice2 (fallback)")
        except Exception as e:
            logger.warning(f"TTS init failed, text-only mode: {e}")

    if tts_adapter is None:
        logger.warning("⚠️ TTS完全不可用！语音回复将被禁用。请启动GPT-SoVITS或配置API Key。")

    # Step 6: Create ASR adapter (speech recognition)
    from white_salary.adapters.asr.siliconflow_adapter import SiliconFlowASRAdapter

    asr_adapter = None
    try:
        # 2026-07-03 审计修复（批5）：ASR 配置化——模型名改读 config.asr.model
        # （默认值=原硬编码 FunAudioLLM/SenseVoiceSmall）；密钥优先用
        # config.asr.api_key，留空时保留原有"扫角色配置找SiliconFlow密钥"的
        # 兜底逻辑（含主对话 llm 节，扫描顺序与旧代码一致，行为不变）
        _sf_key: str = config.asr.api_key
        if not _sf_key:
            for _role in ("llm_vision", "llm_postprocess", "llm_memory"):
                _arc: RoleLLMConfig = getattr(config, _role)
                if "siliconflow" in _arc.provider.lower() and _arc.api_key:
                    _sf_key = _arc.api_key
                    break
            # 主对话 llm 节兜底（旧扫描列表最后一项，结构与角色LLM不同故单独判断）
            if not _sf_key and "siliconflow" in config.llm.provider.lower() and config.llm.api_key:
                _sf_key = config.llm.api_key

        asr_adapter = SiliconFlowASRAdapter(
            api_key=_sf_key,
            model=config.asr.model,
        )
        logger.info("ASR: SiliconFlow SenseVoice")
    except Exception as e:
        logger.warning(f"ASR init failed, voice input disabled: {e}")

    # Step 7: Create Vision adapter
    from white_salary.adapters.vision.multimodal_adapter import MultimodalVisionAdapter

    vision_adapter = None
    # Read vision LLM config
    # 2026-07-03 审计修复（批5）：改读合并后的 config.llm_vision（原 yaml 旁路已删）
    try:
        _vision_conf = config.llm_vision
        if _vision_conf.api_key and _vision_conf.model:
            vision_adapter = MultimodalVisionAdapter(
                api_key=_vision_conf.api_key,
                base_url=_vision_conf.base_url,
                model=_vision_conf.model,
            )
            logger.info(f"Vision: {_vision_conf.provider or 'custom'} / {_vision_conf.model}")
    except Exception as e:
        logger.warning(f"Vision init skipped: {e}")

    # Step 8: Create FastAPI app and register WebSocket route
    from white_salary.infrastructure.server.app import create_app
    from white_salary.infrastructure.server.websocket_handler import handle_chat_websocket
    from white_salary.infrastructure.server.settings_api import (
        get_runtime_instance,
        register_runtime_instance,
    )
    from fastapi import WebSocket as FastAPIWebSocket

    # 2026-07-03 审计修复（批5）：设置面板依赖注入——把运行中的实例装进容器
    # 传给设置API路由，修复"面板写操作与运行实例脱节"（清空对话只清文件、
    # 触发画像学习是假端点等，见 docs/audit-2026-07-02/settings-align.json）。
    # QQ 的上下文管理器在 qq_handler.start_qq_service() 内部创建（晚于此处装配），
    # 用 getter 延迟解析 settings_api 的模块级注册表（注册逻辑见下方 QQ 启动段）。
    runtime_instances: dict = {
        "desktop_agent": agent,
        "user_learning": user_learning,
        "memory_manager": memory_manager,
        "qq_context_manager_getter": lambda: get_runtime_instance("qq_context_manager"),
        # 2026-07-03 面板升级（批6）：人格管理器注入——设置面板保存人设后可调
        # personality.reload() 热重载系统提示词，不再需要重启后端
        "personality": personality,
    }
    # 2026-07-03 面板升级（批6）：同步登记到模块级注册表（键 'personality'），
    # 供不持有 runtime 容器的代码路径经 get_runtime_instance 解析
    register_runtime_instance("personality", personality)

    # 2026-07-03 工具实现（批9）：提醒服务装配——set_reminder 等工具的后端。
    # QQ兜底通知回调在这里注入：经 settings_api 注册表取运行中的QQ适配器与其
    # 事件循环（qq_handler 启动时登记），用 run_coroutine_threadsafe 跨线程调度
    # ——ReminderService 因此不直接依赖qq模块；QQ未启动/未连接时回调返回False，
    # 提醒仍走桌面桥通道（让白开口说出来）。
    from white_salary.core.services.reminder_service import ReminderService

    def _reminder_qq_send(user_id: str, text: str) -> bool:
        """把到点提醒经QQ私聊发给主人（跨线程调度；QQ不可用返回False）。"""
        try:
            _qq_adapter = get_runtime_instance("qq_adapter")
            _qq_loop = get_runtime_instance("qq_loop")
            if _qq_adapter is None or _qq_loop is None:
                return False
            import asyncio as _asyncio_reminder
            _asyncio_reminder.run_coroutine_threadsafe(
                _qq_adapter.send_private_message(user_id, text), _qq_loop,
            )
            return True
        except Exception as _reminder_err:
            logger.warning(f"[Reminder] QQ通道调度失败: {_reminder_err}")
            return False

    _reminder_owner_id: str = (
        str(config.qq.family_qq[0]) if config.qq.family_qq else ""
    )
    reminder_service = ReminderService(
        data_dir=str(project_root / "data"),
        qq_send=_reminder_qq_send,
        owner_id=_reminder_owner_id,
    )
    ReminderService.set_instance(reminder_service)
    # 注册进运行实例注册表（键 'reminders'，供设置面板/未来管理端点解析）
    register_runtime_instance("reminders", reminder_service)
    logger.info(f"[Reminder] 提醒服务就绪（待提醒 {reminder_service.pending_count} 条）")

    fastapi_app = create_app(config, project_root=project_root, runtime=runtime_instances)

    @fastapi_app.websocket("/ws/chat")
    async def websocket_chat(websocket: FastAPIWebSocket) -> None:
        """WebSocket chat endpoint."""
        # 2026-07-02 审计修复（批2）：把user_learning实例传入桌面端WS处理器，
        # 修复原先调用不存在的UserLearningService.get_instance()导致桌面端
        # 画像学习从未运行的问题（QQ端早已传入，桌面端一直拿不到实例）
        await handle_chat_websocket(
            websocket, agent, tts=tts_adapter, asr=asr_adapter,
            vision=vision_adapter, user_learning=user_learning,
        )

    # 2026-07-02 审计修复（批1）：LLM 通道启动自检。
    # 背景：llm_memory 的模型被上游下架后，记忆提取/用户学习静默失败了近一个月。
    # 现在服务启动后会在后台对主模型 + 全部角色通道做 1-token 探活，
    # 坏掉的通道用 ERROR 横幅醒目告警（见 core/services/llm_health.py）。
    from white_salary.core.services.llm_health import check_all_llm_channels
    import asyncio as _asyncio_health

    _health_channels: dict[str, object] = {"llm(主对话)": llm}
    _health_channels.update({f"llm_{_k}": _v for _k, _v in roles.items() if _v})

    @fastapi_app.on_event("startup")
    async def _llm_selfcheck_on_startup() -> None:
        """服务启动后在后台自检所有LLM通道（不阻塞启动、不阻塞请求）。"""
        _asyncio_health.create_task(check_all_llm_channels(_health_channels))

    # 2026-07-03 工具实现（批9）：提醒后台调度在主事件循环内启动
    # （60秒粒度检查到期，懒启动防重；后台任务不占对话路径）
    @fastapi_app.on_event("startup")
    async def _reminder_scheduler_on_startup() -> None:
        """服务启动后启动提醒调度循环（宕机期间错过的提醒首轮补通知）。"""
        reminder_service.ensure_schedule_task()

    # Step 7: Start server
    logger.info(f"HTTP:      http://{host}:{port}")
    logger.info(f"WebSocket: ws://{host}:{port}/ws/chat")
    logger.info(f"Health:    http://{host}:{port}/health")
    logger.info("=" * 60)
    logger.info("  White Salary ready! Waiting for frontend...")
    logger.info("=" * 60)

    # Optional: Start QQ service if enabled
    # 2026-07-03 审计修复（批5）：改读合并后的 config.qq（原 yaml 旁路已删），
    # 各默认值已在 QQConfig 模型中定义且与旧 .get() 默认值一致，行为不变
    qq_conf = config.qq
    qq_enabled = qq_conf.enabled

    if qq_enabled:
        import asyncio
        import threading
        from white_salary.infrastructure.server.qq_handler import start_qq_service

        # 2026-07-03 审计修复（批5）：设置面板依赖注入——QQContextManager 在
        # start_qq_service() 内部创建且 qq_handler.py 未暴露实例（该文件不在本批
        # 修改范围）。最小侵入方案：把 qq_handler 模块命名空间里的 QQContextManager
        # 替换为"创建后自动登记到 settings_api 注册表"的子类（qq_handler 内部按
        # 模块全局名解析该类，调用时取到的即是本子类），行为与父类完全一致，
        # 仅追加注册与公开的 clear_all() 清空方法。
        import white_salary.infrastructure.server.qq_handler as _qq_handler_module
        from white_salary.infrastructure.server.settings_api import register_runtime_instance

        class _RegisteredQQContextManager(_qq_handler_module.QQContextManager):
            """自动注册到 settings_api 运行实例注册表的 QQ 上下文管理器子类。"""

            def __init__(self, *args: object, **kwargs: object) -> None:
                """创建实例后立即登记，供设置面板的清空端点解析。"""
                super().__init__(*args, **kwargs)
                register_runtime_instance("qq_context_manager", self)

            def clear_all(self) -> None:
                """清空全部QQ上下文并立即落盘（设置面板"清空QQ对话上下文"用）。"""
                self._contexts.clear()
                self._save()

        _qq_handler_module.QQContextManager = _RegisteredQQContextManager

        # QQ用独立的ChatAgent + 独立的LLM，避免跟桌面端抢主模型导致限流
        qq_llm = postprocess_llm or background_llm or llm  # 优先用siliconflow2，兜底用主LLM
        qq_agent = ChatAgent(
            llm=qq_llm,
            personality=personality,
            # 2026-07-02 审计修复（批4）：QQ端短期记忆落盘——此前纯内存，
            # 后端一重启QQ的白就忘光刚聊的内容（私聊重启后是零上下文）
            memory=ShortTermMemory(
                max_turns=config.memory.short_term_max_turns,
                persist_path=str(project_root / "data" / "chat_history" / "qq_current.json"),
            ),
            memory_manager=memory_manager,
            tool_registry=tool_registry,
            tool_llm=tool_llm,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            # 2026-07-03 面板升级（批6）：内容过滤开关接活（与桌面端同一开关）
            content_filter_enabled=config.features.content_filter,
        )
        _qq_llm_name = "postprocess" if postprocess_llm else ("background" if background_llm else "main")
        logger.info(f"QQ: 使用独立LLM通道 ({_qq_llm_name})")

        def _run_qq():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # 2026-07-03 审计修复（批5）：参数改读 config.qq 的类型化字段
            loop.run_until_complete(start_qq_service(
                agent=qq_agent,
                ws_url=qq_conf.ws_url,
                bot_name=qq_conf.bot_name,
                token=qq_conf.token,
                family_qq=qq_conf.family_qq,
                user_learning=user_learning,
                asr_adapter=asr_adapter,
                vision_adapter=vision_adapter,
                # 2026-07-03 面板升级（批6）：功能开关下发（topic_tracker/rest_system
                # 在 qq_handler 内消费；user_learning=false 时上面传入的已是 None）
                features=config.features,
            ))

        qq_thread = threading.Thread(target=_run_qq, daemon=True)
        qq_thread.start()
        logger.info(f"QQ: NapCat OneBot v11 ({qq_conf.ws_url})")
    else:
        logger.info("QQ: 未启用（在conf.yaml中设置 qq.enabled: true 开启）")

    # 启动后台服务（记忆整理）
    # 2026-07-03 面板升级（批6）：features.memory_consolidation 接活——关闭时
    # 不启动每日凌晨调度线程（settings_api 的手动整理端点有独立兜底实例化，
    # 不受影响），修复该开关零消费方的问题；默认 true=原行为
    import threading

    if config.features.memory_consolidation:
        def _run_consolidation():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(mem_consolidation.start())

        consolidation_thread = threading.Thread(target=_run_consolidation, daemon=True)
        consolidation_thread.start()
        logger.info("后台服务: 记忆整理已启动（每日凌晨4点自动执行）")
    else:
        logger.info("后台服务: 记忆整理已按 features.memory_consolidation=false 关闭（手动触发仍可用）")

    # 启动QQ空间后台服务（社交管理器+评论监控）
    try:
        from white_salary.core.qzone.social_manager import get_social_manager
        from white_salary.core.services.qzone_monitor import get_qzone_monitor

        # QQ空间必须用独立的ChatAgent（不能跟QQ共用）
        # 原因：共用agent会导致QQ聊天和QQ空间的对话记忆互相污染
        # 独立agent = 独立的ShortTermMemory，但共享人设/长期记忆/好感度
        _qzone_llm = postprocess_llm or background_llm or llm
        _qzone_agent = ChatAgent(
            llm=_qzone_llm,
            personality=personality,
            # 2026-07-02 审计修复（批4）：QQ空间端短期记忆同样落盘
            memory=ShortTermMemory(
                max_turns=config.memory.short_term_max_turns,
                persist_path=str(project_root / "data" / "chat_history" / "qzone_current.json"),
            ),
            memory_manager=memory_manager,
            tool_registry=tool_registry,
            tool_llm=tool_llm,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            # 2026-07-03 面板升级（批6）：内容过滤开关接活（与桌面端同一开关）
            content_filter_enabled=config.features.content_filter,
        )
        logger.info("[QZone] 创建独立ChatAgent（独立对话记忆，共享人设+长期记忆）")

        # 初始化社交管理器（传agent走完整人设流程）
        qzone_social = get_social_manager(agent=_qzone_agent)
        logger.info("[QZone] 社交管理器已初始化")

        # 初始化评论监控（传agent走完整人设流程）
        qzone_monitor = get_qzone_monitor(agent=_qzone_agent)

        # 启动监控后台线程（3小时轮询检查新评论）
        def _run_qzone_monitor():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(qzone_monitor.start())

        qzone_monitor_thread = threading.Thread(target=_run_qzone_monitor, daemon=True)
        qzone_monitor_thread.start()
        logger.info("[QZone] 评论监控已启动（3小时轮询+启动检查未回复）")
    except Exception as e:
        logger.debug(f"[QZone] 后台服务启动跳过: {e}")

    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
