"""
white_salary/infrastructure/server/settings_api.py

Settings REST API for the control panel.

Provides endpoints to read and write White Salary configuration:
  GET  /api/settings          - Read all settings from conf.yaml
  POST /api/settings          - Save settings to conf.yaml
  GET  /api/settings/prompt   - Read system prompt text
  POST /api/settings/prompt   - Save system prompt text
  GET  /api/settings/status   - Get service status (TTS/Backend ports)
  GET  /api/settings/providers - Get list of preset LLM providers

All sensitive fields (API keys) are masked when reading,
but accepted in full when writing.
"""

# 2026-07-02 审计修复（批2）：补 import json——github_read_file/github_write_file 直接调用
# json.loads 但模块从未导入，此前一调用必抛 NameError（被 except 吞掉表现为"GitHub访问失败"）
import json
import socket
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel


# Preset LLM providers (same as factory.py)
PRESET_PROVIDERS = {
    # ===== 已验证可用 =====
    "siliconflow": {
        "name": "✅ 硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3.2",
        "api_key": "",
    },
    "siliconflow2": {
        "name": "✅ 硅基流动 #2 (备用Key)",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3.2",
        "api_key": "",
    },
    "deepseek": {
        "name": "✅ DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "api_key": "",
    },
    "kimi": {
        "name": "✅ Kimi / Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "api_key": "",
    },
    "futureppo": {
        "name": "✅ Futureppo (Claude代理)",
        "base_url": "https://91vip.futureppo.top/v1",
        "default_model": "claude-sonnet-4-6",
        "api_key": "",
    },
    "openrouter": {
        "name": "✅ OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o",
        "api_key": "",
    },
    "nvidia1": {
        "name": "✅ NVIDIA NIM #1 (免费)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "deepseek-ai/deepseek-v3.1",
        "api_key": "",
    },
    "nvidia2": {
        "name": "✅ NVIDIA NIM #2 (免费)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
        "api_key": "",
    },
    # ===== 余额不足 / 需自行充值 =====
    "dmxapi": {
        "name": "⚠️ DMXAPI (余额不足)",
        "base_url": "https://www.dmxapi.cn/v1",
        "default_model": "gpt-4o",
        "api_key": "",
    },
    # ===== 需要自己的Key =====
    "openai": {
        "name": "🔑 OpenAI 官方 (需自行填Key)",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "api_key": "",
    },
    "ollama": {
        "name": "🖥️ Ollama (本地运行)",
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "api_key": "ollama",
    },
}


# 2026-07-03 面板升级（批6）：LLM 连通性测试允许的通道键（与 conf.yaml 配置节一一对应，
# 依据 panel-llm.json"连通性测试按钮"审计项）
LLM_TEST_ROLES: tuple[str, ...] = (
    "llm", "llm_tool", "llm_memory", "llm_emotion",
    "llm_vision", "llm_postprocess", "llm_detect", "llm_background",
)

# 2026-07-03 面板升级（批6）：GPT-SoVITS 推理配置候选路径（声音克隆状态页用；
# train_voice.py step7 每次训练完会更新该文件。提为模块级常量便于单测替换/后续扩展）
# 2026-07-03 外部依赖优化（批8）：候选路径改为按可配置的 GPT-SoVITS 安装目录动态推导
# （external_tools.gpt_sovits_dir，解析顺序：环境变量→配置→内置默认 D:/AI_Tools/GPT-SoVITS）。
# 保留为函数而非模块级常量，因为路径依赖运行期配置；单测仍可 monkeypatch 本函数。
def get_tts_infer_config_candidates() -> tuple[str, ...]:
    """
    返回 GPT-SoVITS 推理配置文件(tts_infer.yaml)的候选路径。

    路径基于可配置的 GPT-SoVITS 安装目录动态推导，换机器时改
    conf.yaml external_tools.gpt_sovits_dir 即可，无需改源码。
    配置解析失败时回退到内置默认目录（行为不变）。

    Returns:
        候选路径元组（当前仅一个：<安装目录>/GPT_SoVITS/configs/tts_infer.yaml）
    """
    try:
        from white_salary.adapters.tools.external_paths import get_gpt_sovits_dir

        sovits_dir = get_gpt_sovits_dir()
    except Exception:
        from pathlib import Path as _Path

        sovits_dir = _Path("D:/AI_Tools/GPT-SoVITS")
    return (str(sovits_dir / "GPT_SoVITS" / "configs" / "tts_infer.yaml"),)


# 向后兼容：保留模块级常量名，import 期用 get_tts_infer_config_candidates() 求值
# 一次（吃到 external_tools.gpt_sovits_dir 配置覆盖；配置不可用时回退内置默认）。
# 单测仍可 monkeypatch 本常量整体替换（voice-clone/status 端点读的就是它）。
TTS_INFER_CONFIG_CANDIDATES: tuple[str, ...] = get_tts_infer_config_candidates()


def _module_description(doc: str | None) -> str:
    """
    2026-07-03 面板升级（批6）：从模块 docstring 提取首个有效行做描述。

    项目内记忆模块的 docstring 首行惯例是文件路径（如
    "white_salary/core/memory/user_filter.py"），真正的中文说明在其后，
    这里跳过空行与路径行，取第一条说明文字。

    参数:
        doc: 模块 __doc__（可为 None）

    返回:
        描述文本；无有效行时返回空串
    """
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        text = line.strip()
        if not text:
            continue
        # 跳过"文件路径"惯例行
        if text.endswith(".py") or text.startswith("white_salary"):
            continue
        return text
    return ""


class SettingsUpdate(BaseModel):
    """Request body for saving settings."""
    settings: dict[str, Any]


# 2026-07-03 审计修复（批5）：运行实例注册表——解决"创建时序晚于路由装配"的组件注入。
# 典型场景：QQ 的 QQContextManager 在 qq_handler.start_qq_service() 内部创建（该文件
# 本批不可改且未暴露实例），run_server.py 用"子类替换"让实例创建后自动登记到这里，
# 设置面板的写操作（清空QQ上下文等）从此能真正触达运行中的内存实例
# （依据 docs/audit-2026-07-02/settings-align.json："写操作与运行实例脱节"）。
_runtime_registry: dict[str, Any] = {}


def register_runtime_instance(name: str, instance: Any) -> None:
    """
    注册一个运行中的实例到模块级注册表。

    参数:
        name: 实例名（如 "qq_context_manager"）
        instance: 运行中的实例对象
    """
    _runtime_registry[name] = instance
    logger.debug(f"[Settings] 运行实例已注册: {name} ({type(instance).__name__})")


def get_runtime_instance(name: str) -> Any | None:
    """
    从模块级注册表取运行中的实例。

    参数:
        name: 实例名

    返回:
        已注册的实例；未注册返回 None
    """
    return _runtime_registry.get(name)


def create_settings_router(
    project_root: Path,
    runtime: dict[str, Any] | None = None,
) -> APIRouter:
    """
    Create the settings API router.

    Args:
        project_root: Path to project root (where conf.yaml lives)
        runtime: 2026-07-03 审计修复（批5）：运行实例容器（可选，全部键可缺失/为 None）。
            支持的键：
              - "desktop_agent": 运行中的桌面端 ChatAgent（清空对话真生效用）
              - "qq_context_manager_getter": 无参回调，返回运行中的 QQContextManager
                （QQ 上下文管理器创建时序晚于路由装配，用 getter 延迟解析）
              - "user_learning": 运行中的 UserLearningService（手动触发画像学习用）
              - "memory_manager": 运行中的 MemoryManager（手动触发记忆整理用）
            不传 runtime（或对应键为 None）时各端点回退到"仅操作文件"的旧行为。

    Returns:
        FastAPI APIRouter with settings endpoints
    """
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    # 2026-07-03 审计修复（批5）：运行实例容器（None 安全）
    _runtime: dict[str, Any] = runtime or {}

    def _get_runtime(name: str) -> Any | None:
        """
        取运行实例：优先 runtime 容器，其次模块级注册表；均无则返回 None。

        参数:
            name: 实例名

        返回:
            运行实例或 None
        """
        value = _runtime.get(name)
        if value is None:
            value = _runtime_registry.get(name)
        return value

    conf_path = project_root / "conf.yaml"
    conf_default_path = project_root / "conf.default.yaml"
    prompt_path = project_root / "prompts" / "system_prompt.txt"

    def _load_config() -> dict:
        """Load merged config (default + user overrides)."""
        config = {}

        # Load defaults
        if conf_default_path.exists():
            with open(conf_default_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

        # Merge user overrides
        if conf_path.exists():
            with open(conf_path, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            _deep_merge(config, user_config)

        return config

    def _save_config(config: dict) -> None:
        """Save user config to conf.yaml."""
        with open(conf_path, "w", encoding="utf-8") as f:
            yaml.dump(
                config,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def _mask_key(key: str) -> str:
        """Mask an API key for display (show first 6 and last 4 chars)."""
        if not key or len(key) < 12:
            return "***" if key else ""
        return key[:6] + "***" + key[-4:]

    def _mask_config(config: dict) -> dict:
        """Mask sensitive fields in config for frontend display."""
        masked = {}
        for k, v in config.items():
            if isinstance(v, dict):
                masked[k] = _mask_config(v)
            elif k in ("api_key",) and isinstance(v, str) and v:
                masked[k] = _mask_key(v)
            else:
                masked[k] = v
        return masked

    def _check_port(host: str, port: int) -> bool:
        """Check if a service is listening on given port."""
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, OSError, socket.timeout):
            return False

    @router.get("")
    async def get_settings() -> dict:
        """Read all settings (API keys masked)."""
        config = _load_config()
        return {"settings": _mask_config(config)}

    @router.get("/full")
    async def get_settings_full() -> dict:
        """Read merged config (defaults + user overrides), keys NOT masked. For control panel use."""
        config = _load_config()
        return {"settings": config}

    @router.get("/raw")
    async def get_settings_raw() -> dict:
        """Read raw user config (conf.yaml only, keys NOT masked)."""
        if conf_path.exists():
            with open(conf_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}
        return {"settings": config}

    @router.post("")
    async def save_settings(body: SettingsUpdate) -> dict:
        """Save settings to conf.yaml with validation."""
        try:
            if not body.settings or not isinstance(body.settings, dict):
                raise HTTPException(status_code=400, detail="Invalid settings format")

            # Don't save masked API keys (if value contains ***)
            cleaned = _clean_masked_keys(body.settings, _load_config())
            # 2026-07-02 审计修复（批2）：改为「读现有 conf.yaml → 深合并前端子集 → 写回」。
            # 原逻辑用前端表单子集整体覆盖 conf.yaml，会静默删掉面板不管理的配置节
            # （如手工添加的 system/server/asr/vad 等）。注意只读用户 conf.yaml 本身，
            # 不用 _load_config()（那会把 conf.default.yaml 的默认值固化进用户配置）。
            existing: dict[str, Any] = {}
            if conf_path.exists():
                with open(conf_path, "r", encoding="utf-8") as f:
                    existing = yaml.safe_load(f) or {}
            _deep_merge(existing, cleaned)  # _deep_merge 是原地修改语义：把 cleaned 合并进 existing
            _save_config(existing)
            logger.info("Settings saved to conf.yaml (deep-merged)")
            return {"status": "ok", "message": "设置已保存，重启后生效"}
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _backup_system_prompt() -> str | None:
        """
        2026-07-03 面板升级（批6）：覆盖 system_prompt.txt 前先自动备份。

        备份到 prompts/backups/system_prompt_<时间戳>.txt，只保留最近10份
        （依据 panel-persona.json"人设模板库"审计项：38KB 精调人设一键蒸发不可逆）。

        返回:
            备份文件路径字符串；原文件不存在或备份失败时返回 None（不阻断保存）
        """
        try:
            if not prompt_path.exists():
                return None
            import time as _time
            backup_dir = prompt_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = _time.strftime("%Y%m%d_%H%M%S")
            backup = backup_dir / f"system_prompt_{stamp}.txt"
            # 同一秒内多次保存时避免互相覆盖
            counter = 1
            while backup.exists():
                backup = backup_dir / f"system_prompt_{stamp}_{counter}.txt"
                counter += 1
            backup.write_text(prompt_path.read_text(encoding="utf-8"), encoding="utf-8")
            # 只保留最近10份（文件名含时间戳，字典序即时间序）
            all_backups = sorted(backup_dir.glob("system_prompt_*.txt"))
            for old in all_backups[:-10]:
                try:
                    old.unlink()
                except Exception as e:
                    logger.warning(f"[Prompt] 清理旧备份 {old.name} 失败: {e}")
            logger.info(f"[Prompt] 系统提示词已备份: {backup.name}")
            return str(backup)
        except Exception as e:
            # 备份失败不阻断保存（保存是用户的直接意图），但要留下日志
            logger.warning(f"[Prompt] 备份系统提示词失败（继续保存）: {e}")
            return None

    def _try_reload_personality() -> bool:
        """
        2026-07-03 面板升级（批6）：提示词落盘后尝试热更新运行中的人格。

        运行实例注册表里若有 'personality'（P3 智能体负责给 PersonalityManager
        加 reload() 并注册），则调用其 reload() 让改动立即生效；
        实例缺失/无 reload 方法/调用异常时返回 False（提示需重启）。

        返回:
            True=已热更新；False=需重启后端才生效
        """
        personality = _get_runtime("personality")
        if personality is None or not hasattr(personality, "reload"):
            return False
        try:
            personality.reload()
            logger.info("[Prompt] 运行中人格已热更新（personality.reload()）")
            return True
        except Exception as e:
            logger.warning(f"[Prompt] 人格热更新失败（需重启后端生效）: {e}")
            return False

    @router.get("/prompt")
    async def get_prompt() -> dict:
        """Read system prompt text."""
        if prompt_path.exists():
            text = prompt_path.read_text(encoding="utf-8")
        else:
            text = ""
        return {"prompt": text}

    @router.post("/prompt")
    async def save_prompt(body: dict) -> dict:
        """Save system prompt text."""
        text = body.get("prompt", "")
        # 2026-07-02 审计修复（批2）：prompt 缺失/为空串/非字符串时返回 400 拒绝写入，
        # 防止误用该端点的调用（如不带 body 或空 body）把 39KB 的 system_prompt.txt 清空
        if not isinstance(text, str) or not text.strip():
            logger.warning("[Prompt] 收到空 prompt 保存请求，已拒绝（防止清空系统提示词）")
            raise HTTPException(status_code=400, detail="prompt 不能为空，已拒绝写入以保护现有系统提示词")
        # 2026-07-03 面板升级（批6）：覆盖前自动备份旧文件（保留最近10份）
        backup_path = _backup_system_prompt()
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(text, encoding="utf-8")
        logger.info(f"System prompt saved ({len(text)} chars)")
        # 2026-07-03 面板升级（批6）：尝试热更新运行中人格，响应注明生效方式
        hot_reloaded = _try_reload_personality()
        return {
            "status": "ok",
            "backup": backup_path,
            "hot_reloaded": hot_reloaded,
            "message": "人设已保存并热更新（立即生效）" if hot_reloaded
            else "人设已保存，重启后端后生效",
        }

    # 2026-07-02 审计修复（批2）：status 整体结果的 10 秒 TTL 缓存（前端每 10 秒轮询，
    # 避免每次都执行 netstat/socket 探测/sqlite 计数）。用闭包内可变字典保存状态。
    _status_cache: dict[str, Any] = {"ts": 0.0, "data": None}

    @router.get("/status")
    async def get_status(force: int = 0) -> dict:
        """Get detailed service status."""
        import asyncio
        import subprocess
        import time

        # 2026-07-02 审计修复（批2）：10 秒内命中缓存直接返回，不重复探测
        # 2026-07-03 面板升级（批6）：支持 ?force=1 跳过缓存——手动点"刷新状态"
        # 按钮时真正即时探测（依据 panel-main.json"刷新状态按钮"审计项）
        now = time.monotonic()
        if not force and _status_cache["data"] is not None and now - _status_cache["ts"] < 10.0:
            return _status_cache["data"]

        # 2026-07-03 面板升级（批6）：QQ/TTS 探测端口从合并配置解析
        # （qq.ws_url / tts.local_api_url），不再硬编码 3004/9880——用户改配置后
        # 状态卡才能跟随；解析失败时回退原硬编码值（行为与现状一致）
        # （依据 panel-main.json"状态卡-QQ"/"状态卡-语音(TTS)"审计项）
        from urllib.parse import urlparse
        tts_host, tts_port = "127.0.0.1", 9880
        qq_host, qq_port = "127.0.0.1", 3004
        try:
            _cfg = _load_config()
            _tts_url = urlparse(str((_cfg.get("tts") or {}).get("local_api_url") or ""))
            if _tts_url.hostname:
                tts_host = _tts_url.hostname
            if _tts_url.port:
                tts_port = _tts_url.port
            _qq_url = urlparse(str((_cfg.get("qq") or {}).get("ws_url") or ""))
            if _qq_url.hostname:
                qq_host = _qq_url.hostname
            if _qq_url.port:
                qq_port = _qq_url.port
        except Exception as e:
            logger.warning(f"[Status] 解析 qq.ws_url / tts.local_api_url 失败，使用默认端口: {e}")

        def _get_pid_on_port(port: int) -> str:
            """Get the PID of the process listening on a port (Windows)."""
            try:
                result = subprocess.run(
                    ["netstat", "-aon"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.strip().split()
                        return parts[-1]  # PID is last column
            except Exception as e:
                logger.warning(f"[Status] netstat 查询端口 {port} 失败: {e}")
            return ""

        def _collect_ports() -> dict[str, Any]:
            """同步收集端口/进程信息（netstat 与 socket 连接均为阻塞调用）。"""
            # 2026-07-03 面板升级（批6）：探测目标改用上方从配置解析出的 host/port
            tts_online = _check_port(tts_host, tts_port)
            return {
                "tts_online": tts_online,
                "tts_pid": _get_pid_on_port(tts_port) if tts_online else "",
                "backend_pid": _get_pid_on_port(12400),
                "qq_connected": _check_port(qq_host, qq_port),
            }

        # 2026-07-02 审计修复（批2）：subprocess.run(netstat)/socket 探测放入线程池，
        # 避免阻塞事件循环（netstat 最长可耗 5 秒）
        ports = await asyncio.to_thread(_collect_ports)

        # 记忆统计
        # 2026-07-02 审计修复（批2）：原来的一次性缓存永不刷新，面板"记忆N条"长期失真；
        # 改为 60 秒 TTL——超时后在线程池里重建 CoreMemoryStore 重新加载
        memory_count = 0
        conversation_count = 0
        try:
            cached = getattr(get_status, "_core_cache", None)
            if cached is None or now - cached[1] >= 60.0:
                from white_salary.core.memory.core_store import CoreMemoryStore
                store = await asyncio.to_thread(
                    CoreMemoryStore, data_dir=str(project_root / "data" / "memory")
                )
                get_status._core_cache = (store, now)
                cached = get_status._core_cache
            memory_count = len(cached[0]._cache)
        except Exception as e:
            logger.warning(f"[Status] 核心记忆统计失败: {e}")

        try:
            from white_salary.core.memory.conversation_log import ConversationLog

            def _count_conversations() -> int:
                """同步查询 sqlite 的对话总数（阻塞调用）。"""
                conv = ConversationLog.get_instance(data_dir=str(project_root / "data" / "memory"))
                return conv.total_count

            # 2026-07-02 审计修复（批2）：sqlite 计数放入线程池，避免阻塞事件循环
            conversation_count = await asyncio.to_thread(_count_conversations)
        except Exception as e:
            logger.warning(f"[Status] 对话计数失败: {e}")

        # 视觉系统状态
        vision_enabled = False
        try:
            import yaml as _vy
            with open(project_root / "conf.yaml", encoding="utf-8") as _cf:
                _vc = _vy.safe_load(_cf) or {}
            _vlm = _vc.get("llm_vision", {})
            vision_enabled = bool(_vlm.get("api_key") and _vlm.get("model"))
        except Exception as e:
            logger.warning(f"[Status] 读取视觉配置失败: {e}")

        result = {
            "backend": True,
            "backend_port": 12400,
            "backend_pid": ports["backend_pid"],
            "tts_local": ports["tts_online"],
            # 2026-07-03 面板升级（批6）：回传实际探测端口（来自配置），不再写死
            "tts_port": tts_port,
            "tts_pid": ports["tts_pid"],
            "qq_connected": ports["qq_connected"],
            "qq_port": qq_port,
            "memory_count": memory_count,
            "conversation_count": conversation_count,
            "vision_enabled": vision_enabled,
        }
        # 2026-07-02 审计修复（批2）：写入 10 秒 TTL 缓存
        _status_cache["ts"] = now
        _status_cache["data"] = result
        return result

    @router.get("/memory")
    async def get_memory() -> dict:
        """获取记忆系统数据（核心记忆+长期记忆统计+好感度+情绪）。"""
        from white_salary.core.memory.core_store import CoreMemoryStore
        from white_salary.core.memory.long_term_store import LongTermMemoryStore
        from white_salary.core.affinity.manager import AffinityManager
        from white_salary.core.memory.emotion_tracker import EmotionTracker

        data_dir = str(project_root / "data" / "memory")
        affinity_dir = str(project_root / "data" / "affinity")

        result = {}

        # 核心记忆
        try:
            core = CoreMemoryStore(data_dir=data_dir)
            entries = core.get_all()
            result["core_memories"] = [
                {"key": e.key, "value": e.value, "category": e.category,
                 "importance": e.importance, "source": e.source}
                for e in entries
            ]
            result["core_stats"] = core.get_stats()
        except Exception:
            result["core_memories"] = []
            result["core_stats"] = {}

        # 长期记忆
        try:
            lt = LongTermMemoryStore(data_dir=data_dir)
            result["long_term_stats"] = lt.get_stats()
            recent = lt.get_recent(limit=20)
            result["long_term_recent"] = [
                {"id": e.id, "content": e.content, "layer": e.layer,
                 "importance": e.importance, "is_highlight": e.is_highlight}
                for e in recent
            ]
        except Exception:
            result["long_term_stats"] = {}
            result["long_term_recent"] = []

        # 好感度
        try:
            aff = AffinityManager(data_dir=affinity_dir)
            result["affinity"] = aff.get_stats()
        except Exception:
            result["affinity"] = {}

        # 情绪
        try:
            emo = EmotionTracker(data_dir=data_dir)
            result["emotion"] = emo.get_stats()
            result["emotion_history"] = emo.get_recent_history(10)
        except Exception:
            result["emotion"] = {}
            result["emotion_history"] = []

        return result

    @router.post("/memory/core")
    async def add_core_memory(body: dict) -> dict:
        """手动添加核心记忆。"""
        from white_salary.core.memory.core_store import CoreMemoryStore
        data_dir = str(project_root / "data" / "memory")
        core = CoreMemoryStore(data_dir=data_dir)
        core.set(
            key=body.get("key", ""),
            value=body.get("value", ""),
            category=body.get("category", "other"),
            source="manual",
            importance=body.get("importance", 5),
        )
        return {"status": "ok"}

    @router.delete("/memory/core/{key}")
    async def delete_core_memory(key: str) -> dict:
        """删除核心记忆。"""
        from white_salary.core.memory.core_store import CoreMemoryStore
        data_dir = str(project_root / "data" / "memory")
        core = CoreMemoryStore(data_dir=data_dir)
        if core.delete(key):
            return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Memory not found")

    @router.get("/memory/search")
    async def search_memory(q: str = "", limit: int = 20) -> dict:
        """
        2026-07-03 面板升级（批6）：长期记忆关键词/语义检索。

        GET /api/settings/memory/search?q=关键词&limit=20
        调 LongTermMemoryStore.search（批5进程级共享实例；有ChromaDB走向量
        语义检索，否则关键词匹配降级，见 panel-memory.json"记忆搜索"审计项）。
        """
        import asyncio
        query = (q or "").strip()
        if not query:
            return {"results": [], "count": 0, "query": ""}
        try:
            limit_n = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit_n = 20
        from white_salary.core.memory import long_term_store as _lt_mod
        try:
            store = _lt_mod.LongTermMemoryStore(
                data_dir=str(project_root / "data" / "memory")
            )
            # search 内部是阻塞的 sqlite/chroma 调用，放线程池避免卡事件循环
            entries = await asyncio.to_thread(store.search, query, limit_n)
            results = [
                {
                    "id": getattr(e, "id", 0),
                    "content": getattr(e, "content", ""),
                    "layer": getattr(e, "layer", ""),
                    "importance": getattr(e, "importance", 0),
                    "is_highlight": getattr(e, "is_highlight", False),
                    "created_at": getattr(e, "created_at", 0.0),
                }
                for e in entries
            ]
            return {"results": results, "count": len(results), "query": query}
        except Exception as e:
            logger.warning(f"[Memory] 记忆检索失败: {e}")
            return {"results": [], "count": 0, "query": query, "error": str(e)}

    @router.post("/affinity/set_family")
    async def set_affinity_family(body: dict) -> dict:
        """设置/取消家人状态。"""
        from white_salary.core.affinity.manager import AffinityManager
        aff = AffinityManager(data_dir=str(project_root / "data" / "affinity"))
        aff.set_family(body.get("is_family", False))
        return {"status": "ok", "is_family": body.get("is_family")}

    @router.post("/affinity/set_points")
    async def set_affinity_points(body: dict) -> dict:
        """手动设置好感度积分。"""
        from white_salary.core.affinity.manager import AffinityManager
        aff = AffinityManager(data_dir=str(project_root / "data" / "affinity"))
        pts = float(body.get("points", 0))
        aff.set_points(pts)
        return {"status": "ok", "points": pts}

    @router.get("/prompt/templates")
    async def get_prompt_templates() -> dict:
        """获取所有人设模板。"""
        templates_dir = project_root / "prompts" / "templates"
        templates = []
        if templates_dir.exists():
            for f in sorted(templates_dir.glob("*.txt")):
                content = f.read_text(encoding="utf-8").strip()
                templates.append({
                    "name": f.stem.replace("_", " ").title(),
                    "file": f.name,
                    "preview": content[:100],
                    "content": content,
                })
        return {"templates": templates}

    @router.post("/prompt/apply_template")
    async def apply_template(body: dict) -> dict:
        """应用一个人设模板（覆盖当前系统提示词）。"""
        template_file = body.get("file", "")
        template_path = project_root / "prompts" / "templates" / template_file
        if not template_path.exists():
            raise HTTPException(status_code=404, detail="Template not found")
        content = template_path.read_text(encoding="utf-8")
        # 2026-07-03 面板升级（批6）：覆盖前自动备份现有 system_prompt.txt 到
        # prompts/backups/（保留最近10份），返回体带备份路径供"恢复上一版"用
        backup_path = _backup_system_prompt()
        prompt_path.write_text(content, encoding="utf-8")
        return {
            "status": "ok",
            "backup": backup_path,
            "message": f"已应用模板: {template_file}"
            + (f"（原人设已备份: {Path(backup_path).name}）" if backup_path else ""),
        }

    @router.post("/restart")
    async def restart_backend() -> dict:
        """Restart the backend server to apply config changes."""
        import os
        import subprocess
        import sys
        logger.info("Backend restart requested from control panel")
        # Schedule restart after response is sent
        import asyncio
        async def _restart() -> None:
            await asyncio.sleep(1)
            # 2026-07-02 审计修复（批2）：os.execv 在 Windows 下把参数按空格拼接且不加引号，
            # 路径含空格（如 D:\White Salary）时新进程参数被拆碎、重启即后端死亡；
            # 改用 subprocess.Popen（列表参数自动正确加引号）拉起新进程后退出当前进程
            subprocess.Popen([sys.executable] + sys.argv, cwd=os.getcwd())
            os._exit(0)
        asyncio.create_task(_restart())
        return {"status": "ok", "message": "Restarting..."}

    @router.post("/start-napcat")
    async def start_napcat() -> dict:
        """Start NapCat QQ bot."""
        import subprocess
        napcat_dir = project_root / "NapCat"
        launcher = napcat_dir / "launcher.bat"
        if not launcher.exists():
            launcher = napcat_dir / "launcher-win10.bat"
        if not launcher.exists():
            raise HTTPException(status_code=404, detail="NapCat launcher not found")
        try:
            proc = subprocess.Popen(
                ["cmd", "/c", "start", str(launcher)],
                cwd=str(napcat_dir),
            )
            # 追踪进程避免僵尸
            if not hasattr(start_napcat, '_processes'):
                start_napcat._processes = []
            start_napcat._processes.append(proc)
            # 清理已结束的
            start_napcat._processes = [p for p in start_napcat._processes if p.poll() is None]
            logger.info(f"NapCat started (pid={proc.pid})")
            return {"status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/logs")
    async def get_logs() -> dict:
        """Get recent log lines from the latest log file."""
        import glob
        log_dir = project_root / "logs"
        log_files = sorted(log_dir.glob("white_salary_*.log"), reverse=True)
        if not log_files:
            return {"logs": ["No log files found."]}

        try:
            with open(log_files[0], "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Return last 100 lines
            return {"logs": [l.rstrip() for l in lines[-100:]]}
        except Exception as e:
            return {"logs": [f"Error reading logs: {e}"]}

    @router.get("/providers")
    async def get_providers() -> dict:
        """Get list of preset LLM providers."""
        return {"providers": PRESET_PROVIDERS}

    @router.post("/llm/test")
    async def test_llm(body: dict) -> dict:
        """
        2026-07-03 面板升级（批6）：LLM 通道连通性测试（1-token 探活）。

        body 两种用法（可混用，直传字段优先于配置值）：
          - {"role": "llm"|"llm_tool"|...}：按合并配置里对应通道的参数测试
          - {"provider","api_key","base_url","model"}：直接用表单未保存的值测试
        复用 core/services/llm_health.check_llm_channel，返回
        {ok, elapsed_ms, error, role, model}（见 panel-llm.json"连通性测试按钮"）。
        """
        import time as _time
        from white_salary.adapters.llm import openai_compatible as _oa
        from white_salary.core.services.llm_health import check_llm_channel

        role = str(body.get("role") or "llm")
        if role not in LLM_TEST_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"role 必须是 {', '.join(LLM_TEST_ROLES)} 之一",
            )

        # 读合并配置里该通道的已保存值做缺省
        section: dict[str, Any] = {}
        try:
            section = _load_config().get(role) or {}
            if not isinstance(section, dict):
                section = {}
        except Exception as e:
            logger.warning(f"[LLM测试] 读取通道 {role} 配置失败: {e}")

        provider = str(body.get("provider") or section.get("provider") or "")
        api_key = str(body.get("api_key") or "")
        # 前端可能把掩码值原样传回（含 ***），此时回退用已保存的真实Key
        if not api_key or "***" in api_key:
            api_key = str(section.get("api_key") or "")
        model = str(body.get("model") or section.get("model") or "")
        if not model:
            model = PRESET_PROVIDERS.get(provider, {}).get("default_model", "")
        base_url = str(body.get("base_url") or section.get("base_url") or "")
        if not base_url:
            base_url = PRESET_PROVIDERS.get(provider, {}).get("base_url", "")

        missing = [
            name for name, value in
            (("api_key", api_key), ("base_url", base_url), ("model", model))
            if not value
        ]
        if missing:
            return {
                "ok": False,
                "elapsed_ms": 0,
                "error": f"通道 {role} 配置不完整，缺少: {', '.join(missing)}",
                "role": role,
                "model": model,
            }

        adapter = _oa.OpenAICompatibleAdapter(
            api_key=api_key, base_url=base_url, model=model, timeout=30.0
        )
        start = _time.monotonic()
        _, ok, reason = await check_llm_channel(role, adapter, timeout=30.0)
        elapsed_ms = int((_time.monotonic() - start) * 1000)
        return {"ok": ok, "elapsed_ms": elapsed_ms, "error": reason, "role": role, "model": model}

    @router.post("/start-tts")
    async def start_tts() -> dict:
        """Start local GPT-SoVITS TTS server."""
        import subprocess

        # 2026-07-03 外部依赖优化（批8）：GPT-SoVITS 安装目录改走统一解析
        # （环境变量 WS_GPT_SOVITS_DIR → conf.yaml external_tools.gpt_sovits_dir → 内置默认
        # D:/AI_Tools/GPT-SoVITS）。cd /d 需要 Windows 反斜杠路径，故转成本机原生写法。
        try:
            from white_salary.adapters.tools.external_paths import get_gpt_sovits_dir

            _sovits_dir = get_gpt_sovits_dir()
        except Exception:
            _sovits_dir = Path("D:/AI_Tools/GPT-SoVITS")
        # os.path.normpath 在 Windows 上把 / 归一为 \，得到 cd /d 认识的原生路径
        import os as _os

        _sovits_dir_win = _os.path.normpath(str(_sovits_dir))
        tts_cmd = (
            'start "WhiteSalary-TTS" cmd /k "'
            f'cd /d {_sovits_dir_win} && '
            'call venv_new\\Scripts\\activate.bat && '
            'python api_v2.py -a 127.0.0.1 -p 9880 '
            '-c GPT_SoVITS/configs/tts_infer.yaml"'
        )
        try:
            proc = subprocess.Popen(tts_cmd, shell=True)
            if not hasattr(start_tts, '_processes'):
                start_tts._processes = []
            start_tts._processes.append(proc)
            start_tts._processes = [p for p in start_tts._processes if p.poll() is None]
            logger.info(f"[TTS] Local GPT-SoVITS start requested (pid={proc.pid})")
            return {"status": "ok", "message": "TTS启动中...模型加载需要约45秒"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/memory/consolidate")
    async def trigger_consolidation() -> dict:
        """手动触发记忆整理。"""
        try:
            from white_salary.core.services.memory_consolidation import MemoryConsolidationService
            from white_salary.core.memory.manager import MemoryManager
            # 2026-07-03 审计修复（批5）：优先用注入的运行中 MemoryManager（与对话
            # 主流程共用同一实例，整理结果立即对运行中记忆生效）；未注入时保留
            # 原行为（新建一次性实例，只整理磁盘数据）
            mm = _get_runtime("memory_manager")
            if mm is None:
                mm = MemoryManager(data_dir=str(project_root / "data" / "memory"))
            svc = MemoryConsolidationService(memory_manager=mm)
            result = await svc.run_now()
            return result
        except Exception as e:
            return {"error": str(e), "duplicates_removed": 0, "expired_removed": 0}

    def _resolve_master_user_id() -> str:
        """
        2026-07-03 审计修复（批5）：解析主人的统一 user_id。

        口径与 websocket_handler._resolve_owner_id 一致：
        取合并配置（conf.default.yaml + conf.yaml）里 qq.family_qq 的第一个号；
        family_qq 为空或读取失败时回退旧值 "desktop"。

        返回:
            主人统一 user_id 字符串
        """
        try:
            config = _load_config()
            family = (config.get("qq") or {}).get("family_qq") or []
            if family:
                return str(family[0])
        except Exception as e:
            logger.warning(f"[Settings] 解析主人统一user_id失败，回退 desktop: {e}")
        return "desktop"

    @router.post("/user-learning/trigger")
    async def trigger_user_learning() -> dict:
        """手动触发用户画像学习。"""
        # 2026-07-03 审计修复（批5）：原实现是假端点（固定返回 success:False，
        # 面板按钮永远无效，见 settings-align.json）。现接入运行中的
        # UserLearningService：对主人统一 user_id 触发一次 learn()，返回真实结果；
        # 未注入实例时维持原文案（行为不变）
        user_learning = _get_runtime("user_learning")
        if user_learning is None:
            return {"success": False, "message": "用户学习会在对话积累足够后自动触发，无需手动操作"}
        try:
            owner_id = _resolve_master_user_id()
            # learn() 是异步公开方法，签名 learn(user_id) -> Optional[dict]
            # （见 core/services/user_learning.py），返回 None 表示未产出画像
            profile = await user_learning.learn(owner_id)
            if profile:
                return {
                    "success": True,
                    "message": f"用户画像分析完成（user_id={owner_id}）",
                    "profile": profile,
                }
            return {
                "success": False,
                "message": (
                    "本次学习未产出画像：分析用LLM未配置、近期消息不足10条或分析失败"
                    "（详见后端日志）"
                ),
            }
        except Exception as e:
            logger.error(f"[Settings] 手动触发用户学习失败: {e}")
            return {"success": False, "message": f"触发用户学习失败: {e}"}

    @router.post("/chat/reset")
    async def reset_chat() -> dict:
        """清空桌面端对话历史。"""
        # 2026-07-03 审计修复（批5）：清空对话真生效（见 settings-align.json：
        # 原实现新建一次性 ShortTermMemory 再 clear()，而 clear() 只清内存不落盘，
        # 文件和运行中 Agent 均不受影响，是完全的 no-op）。
        # 现优先清运行中 desktop_agent 的短期记忆并落盘；未注入时回退为真正清文件。
        try:
            from white_salary.core.memory.short_term import ShortTermMemory

            agent = _get_runtime("desktop_agent")
            if agent is not None:
                # 清运行实例内存（reset_conversation 是 ChatAgent 公开方法）
                agent.reset_conversation()
                # clear() 不落盘（short_term.py:124-128），补一次 _save_to_file
                # 语义的落盘调用（short_term.py 不在本批修改范围，无公开落盘方法，
                # 此处按约定访问其内部落盘方法）
                mem = getattr(agent, "_memory", None)
                if mem is not None and hasattr(mem, "_save_to_file"):
                    mem._save_to_file()
                return {"status": "ok", "message": "运行中对话记忆与历史文件均已清空"}

            # 回退路径：未注入运行实例，仅清文件（运行中记忆需重启后才消失）
            mem = ShortTermMemory(
                persist_path=str(project_root / "data" / "chat_history" / "current.json")
            )
            mem.clear()
            mem._save_to_file()  # clear() 不落盘，补落盘让文件真正清空
            return {
                "status": "ok",
                "message": "后端未注入运行实例，仅清文件；运行中对话记忆需重启后才会消失",
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/qq/clear-context")
    async def clear_qq_context() -> dict:
        """清空QQ对话上下文。"""
        ctx_path = project_root / "data" / "qq" / "contexts.json"
        try:
            # 2026-07-03 审计修复（批5）：清空QQ上下文真生效（见 settings-align.json：
            # 原实现只把 contexts.json 写成 {}，运行中 QQContextManager._contexts
            # 在下一条群消息 add_message→_save() 时整表回写，清空立即无效）。
            # 现通过 getter/注册表解析运行中的上下文管理器实例，先清内存再清文件。
            ctx_manager: Any | None = None
            getter = _get_runtime("qq_context_manager_getter")
            if callable(getter):
                try:
                    ctx_manager = getter()
                except Exception as e:
                    logger.warning(f"[Settings] 解析QQ上下文管理器失败: {e}")
            if ctx_manager is None:
                # getter 缺失/返回 None 时兜底查模块级注册表
                ctx_manager = _runtime_registry.get("qq_context_manager")

            cleared_runtime = False
            if ctx_manager is not None:
                # 2026-07-03 面板升级（批6）：清运行实例抛异常时不再让整个端点走
                # except 分支——注释早已承诺"无论运行实例是否清成功都清文件"，
                # 现在把运行实例操作单独包起来，异常只降级为警告，文件清空照常执行
                try:
                    if hasattr(ctx_manager, "clear_all"):
                        # run_server 注册的子类提供的公开清空方法（清内存+落盘）
                        ctx_manager.clear_all()
                    else:
                        # 兜底：按 qq_handler.QQContextManager 的内部结构清空
                        # （该文件不在本批修改范围，无公开清空方法）
                        ctx_manager._contexts.clear()
                        ctx_manager._save()
                    cleared_runtime = True
                except Exception as e:
                    logger.warning(f"[Settings] 清空运行中QQ上下文失败，仍将清空文件: {e}")

            # 文件兜底：无论运行实例是否清成功，都确保磁盘文件被清空
            if ctx_path.exists():
                ctx_path.write_text("{}", encoding="utf-8")

            if cleared_runtime:
                return {"status": "ok", "message": "QQ对话上下文已清空（运行实例+文件）"}
            # 2026-07-03 面板升级（批6）：区分"未注入"与"清运行实例失败"两种降级
            if ctx_manager is not None:
                return {
                    "status": "ok",
                    "message": "清空运行实例失败（详见日志），已清空文件；运行中QQ上下文需重启后才会消失",
                }
            return {
                "status": "ok",
                "message": "后端未注入运行实例，仅清文件；运行中QQ上下文需重启后才会消失",
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ================================================================
    # 知识图谱 API（第四步）
    # ================================================================

    def _get_kg():
        from white_salary.core.memory.knowledge_graph import KnowledgeGraph
        return KnowledgeGraph(data_dir=str(project_root / "data" / "memory"))

    @router.get("/knowledge")
    async def get_knowledge() -> dict:
        """获取完整知识图谱数据（给前端3D渲染）。"""
        kg = _get_kg()
        return {
            "entities": kg.get_all_entities(),
            "relations": kg.get_all_relations(),
            "stats": kg.get_stats(),
        }

    @router.get("/knowledge/stats")
    async def get_knowledge_stats() -> dict:
        """获取知识图谱统计。"""
        return _get_kg().get_stats()

    @router.post("/knowledge/entity")
    async def add_knowledge_entity(body: dict) -> dict:
        """添加实体。"""
        import json as _json
        # 大小限制：防止超大JSON
        if len(_json.dumps(body, ensure_ascii=False)) > 102400:  # 100KB
            raise HTTPException(status_code=400, detail="请求体过大（最大100KB）")
        kg = _get_kg()
        name = body.get("name", "")
        etype = body.get("type", "person")
        attrs = body.get("attributes", {})
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        if len(name) > 100:
            raise HTTPException(status_code=400, detail="name too long (max 100)")
        entity = kg.add_entity(name, etype, attrs)
        return {"status": "ok", "entity_id": entity.id, "name": entity.name}

    @router.put("/knowledge/entity/{entity_id}")
    async def update_knowledge_entity(entity_id: str, body: dict) -> dict:
        """编辑实体。"""
        kg = _get_kg()
        ok = kg.update_entity(
            entity_id,
            name=body.get("name"),
            entity_type=body.get("type"),
            attributes=body.get("attributes"),
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Entity not found")
        return {"status": "ok"}

    @router.delete("/knowledge/entity/{entity_id}")
    async def delete_knowledge_entity(entity_id: str) -> dict:
        """删除实体（级联删关系）。"""
        kg = _get_kg()
        if not kg.delete_entity(entity_id):
            raise HTTPException(status_code=404, detail="Entity not found")
        return {"status": "ok"}

    @router.post("/knowledge/relation")
    async def add_knowledge_relation(body: dict) -> dict:
        """添加关系。"""
        kg = _get_kg()
        from_name = body.get("from_name", "")
        to_name = body.get("to_name", "")
        relation_type = body.get("relation_type", "")
        importance = float(body.get("importance", 50))
        if not from_name or not to_name or not relation_type:
            raise HTTPException(status_code=400, detail="from_name, to_name, relation_type required")
        rel = kg.add_relation(from_name, relation_type, to_name, importance=importance)
        return {"status": "ok", "relation_id": rel.id if rel else ""}

    @router.put("/knowledge/relation/{relation_id}")
    async def update_knowledge_relation(relation_id: str, body: dict) -> dict:
        """编辑关系。"""
        kg = _get_kg()
        ok = kg.update_relation(
            relation_id,
            relation_type=body.get("relation_type"),
            importance=body.get("importance"),
            properties=body.get("properties"),
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Relation not found")
        return {"status": "ok"}

    @router.delete("/knowledge/relation/{relation_id}")
    async def delete_knowledge_relation(relation_id: str) -> dict:
        """删除关系。"""
        kg = _get_kg()
        if not kg.delete_relation(relation_id):
            raise HTTPException(status_code=404, detail="Relation not found")
        return {"status": "ok"}

    @router.post("/knowledge/query")
    async def query_knowledge(body: dict) -> dict:
        """自然语言查询知识图谱。"""
        kg = _get_kg()
        question = body.get("question", "")
        if not question:
            raise HTTPException(status_code=400, detail="question required")
        result = await kg.query_natural(question)
        return {"answer": result}

    # ================================================================
    # 插件市场 API（第六步）
    # ================================================================

    def _get_market():
        from white_salary.core.plugins.market import PluginMarket
        import json as _j
        token = ""
        repo = "VBHC-UHY/whitesalary-plugins"
        gc_path = project_root / "config" / "github_config.json"
        if gc_path.exists():
            try:
                gc = _j.loads(gc_path.read_text(encoding="utf-8"))
                token = gc.get("token", "")
                repo = gc.get("repo", repo)
            except Exception:
                pass
        return PluginMarket(
            plugins_dir=str(project_root / "plugins"),
            cache_dir=str(project_root / "data" / "cache"),
            github_token=token, github_repo=repo,
        )

    @router.get("/plugins/market/list")
    async def get_market_list() -> dict:
        """获取市场插件列表。"""
        market = _get_market()
        plugins = await market.fetch_list()
        return {"plugins": plugins, "count": len(plugins)}

    @router.get("/plugins/installed")
    async def get_installed_plugins() -> dict:
        """获取已安装插件列表。"""
        market = _get_market()
        return {"plugins": market.get_installed()}

    @router.post("/plugins/install-from-market")
    async def install_plugin(body: dict) -> dict:
        """从市场安装插件。"""
        plugin_id = body.get("plugin_id", "")
        market = _get_market()
        return await market.install(plugin_id)

    @router.post("/plugins/uninstall")
    async def uninstall_plugin(body: dict) -> dict:
        """卸载插件。"""
        plugin_id = body.get("plugin_id", "")
        market = _get_market()
        return market.uninstall(plugin_id)

    @router.post("/plugins/submit")
    async def submit_plugin(body: dict) -> dict:
        """提交插件到市场。"""
        market = _get_market()
        return await market.submit(
            plugin_id=body.get("plugin_id", ""),
            plugin_code=body.get("code", ""),
            metadata=body.get("metadata", {}),
        )

    @router.post("/plugins/market/delete")
    async def delete_market_plugin(body: dict) -> dict:
        """从市场删除插件。"""
        market = _get_market()
        return await market.delete_from_market(
            plugin_id=body.get("plugin_id", ""),
            auth=body.get("auth", ""),
            auth_type=body.get("auth_type", "admin"),
        )

    @router.post("/plugins/sync-to-github")
    async def sync_plugins_to_github() -> dict:
        """同步本地插件到GitHub。"""
        market = _get_market()
        return await market.sync_to_github()

    @router.post("/plugins/toggle")
    async def toggle_plugin(body: dict) -> dict:
        """启用/禁用插件。"""
        market = _get_market()
        return market.toggle_plugin(
            body.get("plugin_id", ""),
            body.get("enabled", True),
        )

    @router.get("/plugins/{plugin_id}/code")
    async def get_plugin_code(plugin_id: str) -> dict:
        """获取插件源代码。"""
        market = _get_market()
        return market.get_plugin_code(plugin_id)

    @router.post("/plugins/{plugin_id}/code")
    async def save_plugin_code(plugin_id: str, body: dict) -> dict:
        """保存插件代码。"""
        market = _get_market()
        return market.save_plugin_code(
            plugin_id, body.get("code", ""),
            metadata=body.get("metadata"),
        )

    @router.post("/plugins/create")
    async def create_plugin(body: dict) -> dict:
        """从模板创建新插件。"""
        market = _get_market()
        return market.create_from_template(
            body.get("plugin_id", ""),
            name=body.get("name", ""),
            description=body.get("description", ""),
        )

    # ================================================================
    # 人设分区编辑API
    # ================================================================

    _SECTION_MARKERS = [
        ("format_rules", "【最高优先级 - 输出格式规则】", "【白 - 角色人设档案】"),
        ("basic_info", "【基本资料】", "【外貌特征"),
        ("appearance", "【外貌特征", "【居住环境】"),
        ("living", "【居住环境】", "【性格特点】"),
        ("personality", "【性格特点】", "【兴趣爱好】"),
        ("hobbies", "【兴趣爱好】", "【白的自述】"),
        ("self_story", "【白的自述】", "【角色设定详解】"),
        ("character_detail", "【角色设定详解】", "【小白指定的绝对执行规则】"),
        ("absolute_rules", "【小白指定的绝对执行规则】", "【自主意識與家人互動規則】"),
        ("autonomy_rules", "【自主意識與家人互動規則】", "【主動搜索規則"),
        ("search_rules", "【主動搜索規則", "【禁止編造記憶"),
        ("memory_rules", "【禁止編造記憶", "【理解表情包"),
        ("image_rules", "【理解表情包", "【遭受攻擊"),
        ("attack_rules", "【遭受攻擊", None),
    ]

    @router.get("/prompt/sections")
    async def get_prompt_sections() -> dict:
        """读取人设各分区。"""
        if not prompt_path.exists():
            return {"sections": {}}
        full_text = prompt_path.read_text(encoding="utf-8")
        sections = {}
        for name, start_marker, end_marker in _SECTION_MARKERS:
            start_idx = full_text.find(start_marker)
            if start_idx == -1:
                continue
            if end_marker:
                end_idx = full_text.find(end_marker, start_idx + len(start_marker))
                if end_idx == -1:
                    content = full_text[start_idx:]
                else:
                    content = full_text[start_idx:end_idx]
            else:
                content = full_text[start_idx:]
            sections[name] = content.strip()
        return {"sections": sections}

    @router.put("/prompt/sections/{section_name}")
    async def update_prompt_section(section_name: str, body: dict) -> dict:
        """更新人设的某个分区。"""
        new_content = body.get("content", "")
        if not new_content:
            raise HTTPException(status_code=400, detail="content is required")
        if not prompt_path.exists():
            raise HTTPException(status_code=404, detail="prompt file not found")

        full_text = prompt_path.read_text(encoding="utf-8")

        # 找到对应分区
        target = None
        for name, start_marker, end_marker in _SECTION_MARKERS:
            if name == section_name:
                target = (start_marker, end_marker)
                break
        if not target:
            raise HTTPException(status_code=404, detail=f"section {section_name} not found")

        start_marker, end_marker = target
        start_idx = full_text.find(start_marker)
        if start_idx == -1:
            raise HTTPException(status_code=404, detail=f"section marker not found in file")

        if end_marker:
            end_idx = full_text.find(end_marker, start_idx + len(start_marker))
            if end_idx == -1:
                new_text = full_text[:start_idx] + new_content + "\n\n"
            else:
                new_text = full_text[:start_idx] + new_content + "\n\n" + full_text[end_idx:]
        else:
            new_text = full_text[:start_idx] + new_content

        prompt_path.write_text(new_text, encoding="utf-8")
        logger.info(f"[Prompt] 分区 {section_name} 已更新 ({len(new_content)}字)")
        # 2026-07-03 面板升级（批6）：分区保存后尝试热更新运行中人格（注册表
        # 'personality' 实例由 P3 注册），响应注明生效方式
        hot_reloaded = _try_reload_personality()
        return {
            "status": "ok",
            "section": section_name,
            "hot_reloaded": hot_reloaded,
            "message": "分区已保存并热更新（立即生效）" if hot_reloaded
            else "分区已保存，重启后端后生效",
        }

    # ================================================================
    # 模块管理API
    # ================================================================

    # 2026-07-03 面板升级（批6）：模块扫描目录改为 project_root 拼接（原相对路径
    # "src/white_salary/core/memory" 依赖后端 CWD=项目根，见 panel-modules.json）
    _memory_module_base = project_root / "src" / "white_salary" / "core" / "memory"

    def _read_disabled_modules() -> set[str]:
        """
        2026-07-03 面板升级（批6）：读取 config/memory_settings.json 的
        modules.disabled（与 manager.py 运行时消费口径一致，按文件名 stem 禁用）。

        返回:
            禁用模块 stem 集合；文件缺失/解析失败返回空集合
        """
        try:
            cfg_path = project_root / "config" / "memory_settings.json"
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                raw = (data.get("modules") or {}).get("disabled") or []
                return {str(n) for n in raw}
        except Exception as e:
            logger.warning(f"[Modules] 读取 modules.disabled 失败: {e}")
        return set()

    @router.get("/modules")
    async def get_modules() -> dict:
        """获取所有记忆模块状态。"""
        modules = []
        # 2026-07-03 面板升级（批6）：enabled 不再硬编码 True——读
        # config/memory_settings.json 的 modules.disabled（运行时 manager.py
        # 确实按它跳过加载），面板显示与真实运行状态对齐；同时返回 stem
        # （供前端开关调 /modules/toggle）与模块 docstring 描述
        disabled_names = _read_disabled_modules()
        try:
            import importlib
            base = _memory_module_base
            from white_salary.core.memory.module_base import MemoryModule
            for d, prefix in [
                (base, "white_salary.core.memory"),
                (base / "enhanced", "white_salary.core.memory.enhanced"),
            ]:
                if not d.exists():
                    continue
                for f in sorted(d.glob("*.py")):
                    if f.name.startswith("_"):
                        continue
                    try:
                        mod = importlib.import_module(f"{prefix}.{f.stem}")
                        if hasattr(mod, "MODULE"):
                            cls = mod.MODULE
                            if issubclass(cls, MemoryModule):
                                inst = cls()
                                modules.append({
                                    "name": inst.name,
                                    "stem": f.stem,
                                    "file": f.name,
                                    "enabled": f.stem not in disabled_names,
                                    "description": _module_description(mod.__doc__),
                                })
                    except Exception:
                        pass
        except Exception:
            pass
        return {"modules": modules, "total": len(modules)}

    @router.post("/modules/toggle")
    async def toggle_module(body: dict) -> dict:
        """
        2026-07-03 面板升级（批6）：启用/禁用记忆模块。

        body: {"stem": 模块文件名(不带.py), "enabled": true/false}
        写 config/memory_settings.json 的 modules.disabled 列表（运行时只在
        启动时扫描加载，改动需重启后生效，见 panel-modules.json"模块开关"审计项）。
        """
        stem = str(body.get("stem") or "").strip()
        if not stem or "enabled" not in body:
            raise HTTPException(status_code=400, detail="需要 stem 与 enabled 字段")
        enabled = bool(body.get("enabled"))

        # 校验 stem 是真实存在的模块文件（防止把任意字符串写进配置）
        base = _memory_module_base
        known_stems: set[str] = set()
        for d in (base, base / "enhanced"):
            if not d.exists():
                continue
            for f in d.glob("*.py"):
                if not f.name.startswith("_"):
                    known_stems.add(f.stem)
        if stem not in known_stems:
            raise HTTPException(status_code=404, detail=f"模块不存在: {stem}")

        cfg_path = project_root / "config" / "memory_settings.json"
        settings: dict[str, Any] = {}
        if cfg_path.exists():
            try:
                settings = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception as e:
                # 配置文件损坏时拒绝写入（整体覆盖会丢失其它配置节）
                raise HTTPException(
                    status_code=500, detail=f"memory_settings.json 解析失败，拒绝覆盖: {e}"
                )
        if not isinstance(settings.get("modules"), dict):
            settings["modules"] = {}
        disabled: list[str] = [str(n) for n in settings["modules"].get("disabled") or []]
        if enabled:
            disabled = [n for n in disabled if n != stem]
        elif stem not in disabled:
            disabled.append(stem)
        settings["modules"]["disabled"] = disabled
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"[Modules] 模块 {stem} 已{'启用' if enabled else '禁用'}（重启后生效）")
        return {
            "status": "ok",
            "stem": stem,
            "enabled": enabled,
            "message": f"模块 {stem} 已{'启用' if enabled else '禁用'}，重启后端后生效",
        }

    # ================================================================
    # 用户管理API
    # ================================================================

    def _resolve_user_filter() -> tuple[Any, bool]:
        """
        2026-07-03 面板升级（批6）：解析用户过滤器实例。

        优先取运行实例注册表的 'user_filter'（P3 智能体负责在 qq_handler 创建后
        注册——运行实例的 add/remove 自带落盘，面板操作即时生效且不会被运行实例
        后续 _save() 整表回写覆盖，见 panel-users.json"拉黑按钮"审计项）；
        拿不到时回退新建文件实例（现行为，重启后生效）。

        返回:
            (过滤器实例, 是否运行实例)
        """
        runtime_uf = _get_runtime("user_filter")
        if runtime_uf is not None:
            # 注册的可能是 MemoryModule 包装（真实例挂在 _impl 上）
            if not hasattr(runtime_uf, "add_to_blacklist") and hasattr(runtime_uf, "_impl"):
                runtime_uf = runtime_uf._impl
            if hasattr(runtime_uf, "add_to_blacklist"):
                return runtime_uf, True
        from white_salary.core.memory.user_filter import UserFilter
        # 2026-07-03 面板升级（批6）：数据目录改用 project_root 拼接，消除CWD依赖
        return UserFilter(data_dir=str(project_root / "data" / "memory")), False

    @router.get("/users/filter")
    async def get_user_filter() -> dict:
        """获取用户过滤状态。"""
        try:
            uf, is_runtime = _resolve_user_filter()
            # 2026-07-03 面板升级（批6）：返回里加黑名单明细（list_blacklist 是
            # 本批给 UserFilter 新加的公开方法），前端可渲染名单+解除按钮
            blacklist = uf.list_blacklist() if hasattr(uf, "list_blacklist") else []
            return {
                "status": "ok",
                "stats": uf.stats,
                "blacklist": blacklist,
                "runtime": is_runtime,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @router.post("/users/filter/blacklist")
    async def add_to_blacklist(body: dict) -> dict:
        """添加用户到黑名单。"""
        user_id = body.get("user_id", "")
        nickname = body.get("nickname", "")
        reason = body.get("reason", "手动拉黑")
        permanent = body.get("permanent", False)
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        try:
            # 2026-07-03 面板升级（批6）：优先操作运行实例（即时生效），回退文件实例
            uf, is_runtime = _resolve_user_filter()
            uf.add_to_blacklist(user_id, nickname, reason, permanent)
            return {
                "status": "ok",
                "runtime": is_runtime,
                "message": "已拉黑（运行实例即时生效）" if is_runtime
                else "已写入黑名单文件；QQ服务运行中的过滤需重启后生效",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/users/filter/blacklist/{user_id}")
    async def remove_from_blacklist(user_id: str) -> dict:
        """从黑名单移除用户。"""
        try:
            # 2026-07-03 面板升级（批6）：优先操作运行实例（即时生效），回退文件实例
            uf, is_runtime = _resolve_user_filter()
            removed = uf.remove_from_blacklist(user_id)
            return {"status": "ok", "removed": bool(removed), "runtime": is_runtime}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/users/affinity/{user_id}/set")
    async def set_user_affinity(user_id: str, body: dict) -> dict:
        """
        2026-07-03 面板升级（批6）：修改指定QQ用户的好感度/家人标记。

        body: {"points": 数字（可选）, "is_family": 布尔（可选）}，至少一项。
        用 AffinityManager.get_for_user（批5共享实例按 user_id 缓存，
        与QQ运行时同一实例，改分即时生效，见 panel-users.json"单个用户好感度修改"）。
        """
        uid = str(user_id).strip()
        if not uid:
            raise HTTPException(status_code=400, detail="user_id 不能为空")
        if "points" not in body and "is_family" not in body:
            raise HTTPException(
                status_code=400, detail="body 至少需要一个字段: points / is_family"
            )
        from white_salary.core.affinity import manager as _aff_mod
        try:
            mgr = _aff_mod.AffinityManager.get_for_user(
                uid, data_dir=str(project_root / "data" / "affinity")
            )
            if "points" in body:
                try:
                    pts = float(body["points"])
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="points 必须是数字")
                mgr.set_points(pts)
            if "is_family" in body:
                mgr.set_family(bool(body["is_family"]))
            stats = mgr.get_stats()
            return {
                "status": "ok",
                "user_id": uid,
                "points": stats.get("points"),
                "is_family": stats.get("is_family"),
                "level_name": stats.get("level_name"),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/users/secrets")
    async def get_secrets() -> dict:
        """获取秘密列表。"""
        try:
            from white_salary.core.memory.secret_system import SecretStore
            store = SecretStore()
            secrets = [{"id": s.secret_id, "content": s.content[:50],
                        "told_by": s.told_by_name, "level": s.level}
                       for s in store.get_all_secrets()]
            return {"secrets": secrets}
        except Exception as e:
            return {"secrets": [], "error": str(e)}

    @router.delete("/users/secrets/{secret_id}")
    async def delete_secret(secret_id: str) -> dict:
        """删除秘密。"""
        try:
            from white_salary.core.memory.secret_system import SecretStore
            store = SecretStore()
            store.remove_secret(secret_id)
            return {"status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/users/impressions")
    async def get_impressions() -> dict:
        """获取对所有人的情感印象。"""
        try:
            from white_salary.core.memory.emotion_memory import EmotionMemoryStore
            store = EmotionMemoryStore()
            return {"impressions": store.get_all_impressions()}
        except Exception as e:
            return {"impressions": {}, "error": str(e)}

    @router.get("/users/affinity/all")
    async def get_all_affinity() -> dict:
        """获取所有用户的好感度。"""
        from pathlib import Path as _P
        users_dir = _P("data/affinity/users")
        result = []
        if users_dir.exists():
            import json
            for f in users_dir.glob("affinity_*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    uid = f.stem.replace("affinity_", "")
                    result.append({
                        "user_id": uid,
                        "points": round(data.get("points", 0), 1),
                        "is_family": data.get("is_family", False),
                        "consecutive_days": data.get("consecutive_days", 0),
                    })
                except Exception:
                    pass
        result.sort(key=lambda x: x["points"], reverse=True)
        return {"users": result}

    # ================================================================
    # 记忆参数API
    # ================================================================

    @router.get("/memory/settings")
    async def get_memory_settings() -> dict:
        """读取记忆系统参数。"""
        import json
        from pathlib import Path as _P
        cfg_path = _P("config/memory_settings.json")
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        return {}

    @router.put("/memory/settings")
    async def update_memory_settings(body: dict) -> dict:
        """更新记忆系统参数。"""
        import json
        from pathlib import Path as _P
        cfg_path = _P("config/memory_settings.json")
        cfg_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[Memory] 记忆参数已更新")
        return {"status": "ok"}

    # ================================================================
    # 防人机设置API
    # ================================================================

    @router.get("/humanlike/status")
    async def get_humanlike_status() -> dict:
        """获取防人机设置状态。"""
        try:
            from white_salary.core.memory.dialogue_evolution import DialogueEvolution
            de = DialogueEvolution()
            return {
                "dialogue_evolution": de.stats,
                "enabled": True,
            }
        except Exception as e:
            return {"error": str(e)}

    # ================================================================
    # 表情包管理API
    # ================================================================

    def _get_sticker_manager():
        if not hasattr(_get_sticker_manager, '_sm'):
            from white_salary.adapters.platform.sticker_manager import StickerManager
            sm = StickerManager(data_dir=str(project_root / "data"))
            sm.init()
            _get_sticker_manager._sm = sm
        return _get_sticker_manager._sm

    @router.get("/stickers")
    async def list_stickers() -> dict:
        """列出所有表情包。"""
        sm = _get_sticker_manager()
        stickers = []
        for sid, info in sm._stickers.items():
            stickers.append({
                "id": sid,
                "desc": info.get("desc", ""),
                "path": info.get("path", ""),
                "preview_url": f"/sticker/{info.get('path', '')}",
            })
        return {"stickers": stickers, "total": len(stickers)}

    @router.post("/stickers/upload")
    async def upload_sticker(body: dict) -> dict:
        """上传新表情包（base64编码或文件路径）。"""
        import hashlib, base64
        sm = _get_sticker_manager()
        sticker_dir = project_root / "data" / "sticker"
        sticker_dir.mkdir(parents=True, exist_ok=True)

        b64_data = body.get("data", "")
        desc = body.get("desc", "")
        filename_hint = body.get("filename", "sticker.jpg")

        if not b64_data:
            return {"status": "error", "message": "缺少图片数据(data字段，base64编码)"}

        content = base64.b64decode(b64_data)
        md5 = hashlib.md5(content).hexdigest().upper()
        ext = Path(filename_hint).suffix.lower() or ".jpg"
        filename = f"{md5}{ext}"
        filepath = sticker_dir / filename
        filepath.write_bytes(content)

        new_id = sm.register(filename, description=desc)
        logger.info(f"[Sticker] 上传: {filename} (id={new_id})")
        return {"status": "ok", "id": new_id, "filename": filename}

    @router.delete("/stickers/{sticker_id}")
    async def delete_sticker(sticker_id: str) -> dict:
        """删除表情包。"""
        sm = _get_sticker_manager()
        if sticker_id not in sm._stickers:
            raise HTTPException(status_code=404, detail="sticker not found")

        info = sm._stickers.pop(sticker_id)
        # 删除文件
        filepath = project_root / "data" / "sticker" / info.get("path", "")
        if filepath.exists():
            filepath.unlink()
        # 更新轮换顺序
        if sticker_id in sm._order:
            sm._order.remove(sticker_id)
        sm._save_config()
        return {"status": "ok"}

    @router.put("/stickers/{sticker_id}")
    async def update_sticker(sticker_id: str, body: dict) -> dict:
        """更新表情包描述/标签。"""
        sm = _get_sticker_manager()
        if sticker_id not in sm._stickers:
            raise HTTPException(status_code=404, detail="sticker not found")
        if "desc" in body:
            sm._stickers[sticker_id]["desc"] = body["desc"]
        if "tags" in body:
            sm._stickers[sticker_id]["tags"] = body["tags"]
        sm._save_config()
        return {"status": "ok"}

    # ================================================================
    # 表情映射 / Live2D / 声音克隆 API
    # 2026-07-03 面板升级（批6）新增（依据 panel-expressions.json /
    # panel-voiceclone.json 审计项）
    # ================================================================

    _expression_map_path = project_root / "config" / "expression_map.json"

    @router.get("/expression-map")
    async def get_expression_map() -> dict:
        """
        读取情绪→Live2D表情映射。

        优先读 config/expression_map.json；文件缺失/损坏时回退
        EmotionTracker.EXPRESSION_MAP 硬编码表（16种情绪）。
        消费侧改造由 P3 智能体负责，本端点只管存取。
        """
        from white_salary.core.memory.emotion_tracker import EmotionTracker
        emap: dict[str, Any] = {k: dict(v) for k, v in EmotionTracker.EXPRESSION_MAP.items()}
        source = "default"
        if _expression_map_path.exists():
            try:
                data = json.loads(_expression_map_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    # 只采纳合法情绪键，文件里的未知键忽略（防脏数据污染返回）
                    for key, value in data.items():
                        if key in emap and isinstance(value, dict):
                            emap[key] = value
                    source = "file"
            except Exception as e:
                logger.warning(f"[ExpressionMap] 读取 expression_map.json 失败，用默认表: {e}")
        return {
            "map": emap,
            "emotions": sorted(EmotionTracker.EXPRESSION_MAP.keys()),
            "source": source,
        }

    @router.put("/expression-map")
    async def update_expression_map(body: dict) -> dict:
        """
        保存情绪→Live2D表情映射（校验情绪键合法）。

        body: {"map": {情绪: 映射}} 或直接顶层就是映射字典。
        每个映射值可以是完整对象 {expression, motion_group, mouth_form}，
        也可以是表情名字符串（自动补齐该情绪的默认 motion/mouth 参数）。
        未提交的情绪保留现有文件值（首次保存时为硬编码默认值），保证文件始终16键完整。
        """
        from white_salary.core.memory.emotion_tracker import EmotionTracker
        raw = body.get("map") if isinstance(body.get("map"), dict) else body
        if not isinstance(raw, dict) or not raw:
            raise HTTPException(status_code=400, detail="map 不能为空")

        valid_keys = set(EmotionTracker.EXPRESSION_MAP.keys())
        unknown = set(raw.keys()) - valid_keys
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"非法情绪键: {sorted(unknown)}（合法键: {sorted(valid_keys)}）",
            )

        # 底表：现有文件（若可读）叠在硬编码默认表上，保证输出16键完整
        merged: dict[str, dict] = {k: dict(v) for k, v in EmotionTracker.EXPRESSION_MAP.items()}
        if _expression_map_path.exists():
            try:
                existing = json.loads(_expression_map_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    for key, value in existing.items():
                        if key in merged and isinstance(value, dict):
                            merged[key] = dict(value)
            except Exception as e:
                logger.warning(f"[ExpressionMap] 现有文件解析失败，按默认表合并: {e}")

        for emotion, value in raw.items():
            base = merged[emotion]
            if isinstance(value, str):
                if not value.strip():
                    raise HTTPException(
                        status_code=400, detail=f"情绪 {emotion} 的表情名不能为空"
                    )
                base["expression"] = value.strip()
            elif isinstance(value, dict):
                base.update(value)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"情绪 {emotion} 的映射必须是表情名字符串或对象",
                )
            merged[emotion] = base

        _expression_map_path.parent.mkdir(parents=True, exist_ok=True)
        _expression_map_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[ExpressionMap] 情绪→表情映射已保存")
        return {"status": "ok", "map": merged, "message": "表情映射已保存"}

    @router.get("/live2d/expressions")
    async def get_live2d_expressions() -> dict:
        """
        枚举当前Live2D模型的可用表情（38个）。

        读 live2d_models/default/ulvm2_0001.model3.json 的
        FileReferences.Expressions，返回 Name 列表——面板表情池不再硬编码6个
        （见 panel-expressions.json"可用表情池"审计项）。
        """
        model_path = (
            project_root / "live2d_models" / "default" / "ulvm2_0001.model3.json"
        )
        if not model_path.exists():
            return {"expressions": [], "count": 0, "error": f"模型文件不存在: {model_path.name}"}
        try:
            data = json.loads(model_path.read_text(encoding="utf-8"))
            exprs = (data.get("FileReferences") or {}).get("Expressions") or []
            names = [
                str(e.get("Name"))
                for e in exprs
                if isinstance(e, dict) and e.get("Name")
            ]
            return {"expressions": names, "count": len(names)}
        except Exception as e:
            logger.warning(f"[Live2D] 解析模型表情列表失败: {e}")
            return {"expressions": [], "count": 0, "error": str(e)}

    @router.get("/voice-clone/status")
    async def get_voice_clone_status() -> dict:
        """
        读取当前声音克隆模型状态。

        读 GPT-SoVITS 的 tts_infer.yaml（train_voice.py step7 每次训练完更新），
        返回当前 GPT/SoVITS 权重路径与版本；配置文件读不到返回 available:false
        （见 panel-voiceclone.json"当前语音模型信息卡"审计项）。
        """
        cfg_path: Path | None = None
        for cand in TTS_INFER_CONFIG_CANDIDATES:
            p = Path(cand)
            if p.exists():
                cfg_path = p
                break
        if cfg_path is None:
            return {
                "available": False,
                "error": "未找到 GPT-SoVITS 推理配置文件（tts_infer.yaml），"
                         "请确认 GPT-SoVITS 已安装",
            }
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            custom = data.get("custom") or {}
            if not isinstance(custom, dict):
                custom = {}
            return {
                "available": True,
                "config_path": str(cfg_path),
                "gpt_weights": str(custom.get("t2s_weights_path") or ""),
                "sovits_weights": str(custom.get("vits_weights_path") or ""),
                "version": str(custom.get("version") or ""),
                "device": str(custom.get("device") or ""),
            }
        except Exception as e:
            logger.warning(f"[VoiceClone] 读取 tts_infer.yaml 失败: {e}")
            return {"available": False, "error": f"读取推理配置失败: {e}"}

    # ================================================================
    # 对话提示模板API
    # ================================================================

    @router.get("/prompt-templates")
    async def get_prompt_templates() -> dict:
        """读取所有对话提示模板。"""
        import json
        from pathlib import Path as _P
        path = _P("config/prompt_templates.json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    @router.put("/prompt-templates")
    async def update_prompt_templates(body: dict) -> dict:
        """更新对话提示模板。"""
        import json
        from pathlib import Path as _P
        path = _P("config/prompt_templates.json")
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[PromptTemplates] 对话提示模板已更新")
        return {"status": "ok"}

    @router.put("/prompt-templates/{section}")
    async def update_prompt_template_section(section: str, body: dict) -> dict:
        """更新单个模板分区。"""
        import json
        from pathlib import Path as _P
        path = _P("config/prompt_templates.json")
        if path.exists():
            templates = json.loads(path.read_text(encoding="utf-8"))
        else:
            templates = {}
        templates[section] = body.get("content", body)
        path.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[PromptTemplates] 模板分区 {section} 已更新")
        return {"status": "ok"}

    # ================================================================
    # 开发者平台API
    # ================================================================

    def _get_dev_manager():
        if not hasattr(_get_dev_manager, '_dm'):
            from white_salary.core.plugins.developer import DeveloperManager
            _get_dev_manager._dm = DeveloperManager()
        return _get_dev_manager._dm

    @router.post("/developers/register")
    async def developer_register(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.register(body.get("username", ""), body.get("password", ""))

    @router.post("/developers/login")
    async def developer_login(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.login(body.get("username", ""), body.get("password", ""))

    @router.post("/developers/logout")
    async def developer_logout(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.logout(body.get("token", ""))

    @router.get("/developers/list")
    async def list_developers(token: str = "") -> dict:
        # 2026-07-03 面板升级（批6）：名单接口要求有效token（?token=xxx）——
        # 原来无鉴权，任何能访问面板端口的人都可枚举全部账号
        # （见 panel-developer.json 安全项）
        dm = _get_dev_manager()
        if not dm.verify_token(token or ""):
            raise HTTPException(status_code=401, detail="需要有效的开发者token（?token=xxx）")
        return {"developers": dm.list_developers(), "stats": dm.stats}

    @router.post("/developers/approve")
    async def approve_developer(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.approve(body.get("username", ""), body.get("token", ""))

    @router.post("/developers/reject")
    async def reject_developer(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.reject(body.get("username", ""), body.get("token", ""))

    @router.post("/developers/set-admin")
    async def set_developer_admin(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.set_admin(body.get("username", ""), body.get("token", ""))

    @router.post("/developers/remove-admin")
    async def remove_developer_admin(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.remove_admin(body.get("username", ""), body.get("token", ""))

    @router.post("/developers/delete")
    async def delete_developer(body: dict) -> dict:
        dm = _get_dev_manager()
        return dm.delete_developer(body.get("username", ""), body.get("token", ""))

    @router.post("/developers/verify")
    async def verify_developer_token(body: dict) -> dict:
        dm = _get_dev_manager()
        result = dm.verify_token(body.get("token", ""))
        if result:
            return {"success": True, **result}
        return {"success": False, "message": "token无效或已过期"}

    # ================================================================
    # GitHub通用文件编辑API
    # ================================================================

    @router.get("/github/file")
    async def github_read_file(repo: str = "", path: str = "") -> dict:
        """读取GitHub仓库文件。"""
        if not repo or not path:
            raise HTTPException(status_code=400, detail="repo and path required")
        try:
            import aiohttp
            gc = {}
            gc_path = project_root / "config" / "github_config.json"
            if gc_path.exists():
                gc = json.loads(gc_path.read_text(encoding="utf-8"))
            token = gc.get("token", "")
            headers = {"Accept": "application/vnd.github.v3+json"}
            if token:
                headers["Authorization"] = f"token {token}"

            async with aiohttp.ClientSession() as session:
                url = f"https://api.github.com/repos/{repo}/contents/{path}"
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return {"success": False, "status": resp.status}
                    data = await resp.json()
                    import base64
                    content = base64.b64decode(data["content"]).decode("utf-8")
                    return {"success": True, "content": content, "sha": data.get("sha")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @router.put("/github/file")
    async def github_write_file(body: dict) -> dict:
        """写入GitHub仓库文件（直接提交到main分支）。"""
        repo = body.get("repo", "")
        path = body.get("path", "")
        content = body.get("content", "")
        message = body.get("message", f"Update {path}")
        if not repo or not path:
            raise HTTPException(status_code=400, detail="repo and path required")
        try:
            import aiohttp, base64
            gc = {}
            gc_path = project_root / "config" / "github_config.json"
            if gc_path.exists():
                gc = json.loads(gc_path.read_text(encoding="utf-8"))
            token = gc.get("token", "")
            if not token:
                return {"success": False, "message": "未配置GitHub Token"}
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }

            async with aiohttp.ClientSession() as session:
                url = f"https://api.github.com/repos/{repo}/contents/{path}"
                # 获取SHA（如果文件已存在）
                sha = None
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sha = data.get("sha")

                # 写入
                put_body = {
                    "message": message,
                    "content": base64.b64encode(content.encode("utf-8")).decode(),
                }
                if sha:
                    put_body["sha"] = sha
                async with session.put(url, headers=headers, json=put_body) as resp:
                    if resp.status in (200, 201):
                        return {"success": True, "message": f"已提交: {path}"}
                    else:
                        text = await resp.text()
                        return {"success": False, "status": resp.status, "error": text[:200]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ================================================================
    # B站相关API
    # ================================================================

    @router.get("/bilibili/status")
    async def bilibili_status() -> dict:
        """获取B站功能状态。"""
        from pathlib import Path as _P
        ini_path = _P("config/bili.ini")
        has_cookie = False
        if ini_path.exists():
            content = ini_path.read_text(encoding="utf-8")
            has_cookie = "sessdata" in content.lower() and len(content) > 50
        # 学习统计
        learning_stats = {}
        try:
            from white_salary.core.bilibili_learning import BiliLearningManager
            bl = BiliLearningManager()
            learning_stats = bl.stats
        except Exception:
            pass
        return {
            "cookie_configured": has_cookie,
            "cookie_path": str(ini_path),
            "learning": learning_stats,
        }

    @router.post("/bilibili/auto-cookie")
    async def bilibili_auto_cookie() -> dict:
        """方案A：自动从浏览器读取B站cookie。"""
        try:
            from white_salary.adapters.tools.bili_cookie_reader import auto_update_bili_ini
            return auto_update_bili_ini()
        except Exception as e:
            return {"success": False, "message": str(e)}

    @router.post("/bilibili/qr-generate")
    async def bilibili_qr_generate() -> dict:
        """方案B-1：生成B站登录二维码。"""
        from white_salary.adapters.tools.bili_qr_login import generate_qr_code
        return await generate_qr_code()

    @router.post("/bilibili/qr-poll")
    async def bilibili_qr_poll(body: dict) -> dict:
        """方案B-2：轮询扫码状态。"""
        qr_key = body.get("qr_key", "")
        if not qr_key:
            return {"success": False, "message": "缺少qr_key"}
        from white_salary.adapters.tools.bili_qr_login import poll_qr_status
        return await poll_qr_status(qr_key)

    @router.post("/bilibili/manual-cookie")
    async def bilibili_manual_cookie(body: dict) -> dict:
        """方案C：手动填写Cookie。"""
        sessdata = body.get("sessdata", "")
        bili_jct = body.get("bili_jct", "")
        if not sessdata:
            return {"success": False, "message": "SESSDATA不能为空"}
        import configparser
        from pathlib import Path as _P
        ini = _P("config/bili.ini")
        ini.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.RawConfigParser()
        cp.add_section("bili")
        cp.set("bili", "sessdata", sessdata)
        cp.set("bili", "bili_jct", bili_jct)
        cp.set("bili", "buvid3", body.get("buvid3", ""))
        cp.set("bili", "dedeuserid", body.get("dedeuserid", ""))
        cp.set("bili", "ac_time_value", "")
        with open(ini, "w", encoding="utf-8") as f:
            cp.write(f)
        return {"success": True, "message": "Cookie已保存"}

    @router.get("/bilibili/check-login")
    async def bilibili_check_login() -> dict:
        """检查B站登录状态。"""
        from white_salary.adapters.tools.bili_qr_login import check_login_status
        return await check_login_status()

    # ================================================================
    # ComfyUI 本地AI生成 API
    # ================================================================

    @router.get("/comfyui/status")
    async def comfyui_status() -> dict:
        """获取ComfyUI状态（是否在线+可用模型列表）。"""
        try:
            from white_salary.adapters.tools.comfyui_client import is_comfyui_online, list_models
            online = await is_comfyui_online()
            models = await list_models() if online else []
            return {
                "online": online,
                "models": models,
                "url": "http://127.0.0.1:8188",
            }
        except Exception as e:
            return {"online": False, "models": [], "error": str(e)}

    @router.post("/comfyui/start")
    async def comfyui_start() -> dict:
        """手动启动ComfyUI。"""
        try:
            from white_salary.adapters.tools.comfyui_client import ensure_comfyui_running
            ok = await ensure_comfyui_running(timeout=90)
            if ok:
                from white_salary.adapters.tools.comfyui_client import list_models
                models = await list_models()
                return {"success": True, "message": "ComfyUI已启动", "models": models}
            return {"success": False, "message": "ComfyUI启动超时（90秒），请检查GPU是否可用"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @router.post("/comfyui/test")
    async def comfyui_test(body: dict = None) -> dict:
        """测试生成一张图片（可指定供应商）。"""
        body = body or {}
        prompt = body.get("prompt", "1girl, silver hair, blue eyes, smile, upper body, simple background, high quality, masterpiece")
        quality = body.get("quality", "fast")
        provider = body.get("provider", "auto")  # auto/comfyui/dmxapi/siliconflow
        try:
            import yaml
            conf = yaml.safe_load(open(str(project_root / "conf.yaml"), encoding="utf-8")) or {}
            sf_key = conf.get("llm_vision", {}).get("api_key", "")
            dmx_key = conf.get("llm", {}).get("api_key", "")

            path = None
            if provider == "auto":
                from white_salary.adapters.tools.image_gen import generate_image
                path = await generate_image(prompt=prompt, siliconflow_key=sf_key, dmxapi_key=dmx_key)
            elif provider == "comfyui":
                from white_salary.adapters.tools.image_gen import _try_comfyui, _get_appearance, _is_self_portrait, _get_negative, _load_style_config
                from white_salary.adapters.tools.comfyui_client import generate_image as _comfyui_gen, ensure_comfyui_running
                if not await ensure_comfyui_running(timeout=60):
                    return {"success": False, "message": "ComfyUI未启动"}
                full_prompt = f"{_get_appearance()}, {prompt}" if _is_self_portrait(prompt) else prompt
                # 支持前端指定模型（不指定就用配置里的）
                test_model = body.get("model", "")
                if test_model:
                    cfg = _load_style_config()
                    neg = cfg.get("negative_prompt", "")
                    q = body.get("quality", "fast")
                    path = await _comfyui_gen(
                        prompt=full_prompt, negative_prompt=neg,
                        model=test_model, width=1024, height=1024, quality=q,
                    )
                else:
                    path = await _try_comfyui(full_prompt, "1024x1024", _is_self_portrait(prompt))
                if path:
                    from white_salary.adapters.tools.image_gen import _download_and_save
                    path = await _download_and_save(path)
            elif provider == "dmxapi":
                from white_salary.adapters.tools.image_gen import _try_dmxapi, _download_and_save
                result = await _try_dmxapi(prompt, dmx_key, "1024x1024")
                if result:
                    path = await _download_and_save(result)
            elif provider == "siliconflow":
                from white_salary.adapters.tools.image_gen import _try_siliconflow, _download_and_save
                result = await _try_siliconflow(prompt, sf_key, "1024x1024")
                if result:
                    path = await _download_and_save(result)

            if path:
                return {"success": True, "path": path, "message": f"图片生成成功（{provider}）"}
            return {"success": False, "message": f"{provider}生成失败"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    from starlette.requests import Request as StarletteRequest
    @router.post("/upload-temp")
    async def upload_temp(request: StarletteRequest) -> dict:
        """上传临时文件（图片修改等用）。接收base64或multipart。"""
        try:
            import time as _time
            temp_dir = project_root / "data" / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)

            content_type = request.headers.get("content-type", "")

            if "json" in content_type:
                # JSON方式：{"filename": "x.png", "data": "base64..."}
                import base64
                body = await request.json()
                filename = body.get("filename", "upload.png")
                data = base64.b64decode(body.get("data", ""))
            else:
                # multipart方式
                form = await request.form()
                file = form.get("file")
                if not file:
                    return {"success": False, "message": "没有文件"}
                filename = getattr(file, "filename", "upload.png")
                data = await file.read()

            if len(data) > 50 * 1024 * 1024:
                return {"success": False, "message": "文件太大（最大50MB）"}

            safe_name = filename.replace("/", "_").replace("\\", "_")
            save_name = f"{int(_time.time())}_{safe_name}"
            save_path = temp_dir / save_name
            save_path.write_bytes(data)
            return {"success": True, "path": str(save_path)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @router.post("/comfyui/img2img")
    async def comfyui_img2img(body: dict = None) -> dict:
        """图片修改（img2img）。"""
        body = body or {}
        prompt = body.get("prompt", "")
        image_path = body.get("image_path", "")
        denoise = float(body.get("denoise", 0.5))

        if not prompt or not image_path:
            return {"success": False, "message": "需要图片路径和修改描述"}

        try:
            from white_salary.adapters.tools.image_gen import edit_image
            result = await edit_image(image_path, prompt, denoise)
            if result:
                return {"success": True, "path": result, "message": "图片修改成功"}
            return {"success": False, "message": "图片修改失败"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @router.get("/comfyui/config")
    async def comfyui_config_get() -> dict:
        """读取AI生成配置。"""
        import json as _json
        cfg_path = project_root / "config" / "image_style.json"
        if cfg_path.exists():
            return _json.loads(cfg_path.read_text(encoding="utf-8"))
        return {}

    @router.put("/comfyui/config")
    async def comfyui_config_put(body: dict) -> dict:
        """更新AI生成配置。"""
        import json as _json
        cfg_path = project_root / "config" / "image_style.json"
        cfg_path.write_text(_json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "ok", "message": "配置已保存"}

    @router.post("/comfyui/test-video")
    async def comfyui_test_video(body: dict = None) -> dict:
        """测试生成视频（可指定供应商）。"""
        body = body or {}
        prompt = body.get("prompt", "1girl, silver white hair, smile, gentle wind, anime, masterpiece")
        mode = body.get("mode", "auto")  # auto/cloud/local_wan22/local_svd
        size = body.get("size", "1280x720")
        try:
            import yaml
            conf = yaml.safe_load(open(str(project_root / "conf.yaml"), encoding="utf-8")) or {}
            sf_key = conf.get("llm_vision", {}).get("api_key", "")
            path = None

            if mode in ("auto", "cloud"):
                from white_salary.adapters.tools.video_gen import generate_video_from_text
                path = await generate_video_from_text(prompt=prompt, api_key=sf_key, size=size)
                if path:
                    return {"success": True, "path": path, "message": f"视频生成成功（云端Wan2.2）"}
                if mode == "cloud":
                    return {"success": False, "message": "云端生成失败（可能被安全过滤拦截）"}

            if mode in ("auto", "local_wan22"):
                from white_salary.adapters.tools.comfyui_client import ensure_comfyui_running, generate_video_wan22
                if await ensure_comfyui_running(timeout=60):
                    path = await generate_video_wan22(input_image="white_new.png", prompt=prompt)
                    if path:
                        return {"success": True, "path": path, "message": "视频生成成功（本地Wan2.2 NSFW）"}
                if mode == "local_wan22":
                    return {"success": False, "message": "本地Wan2.2生成失败"}

            if mode in ("auto", "local_svd"):
                from white_salary.adapters.tools.comfyui_client import ensure_comfyui_running, generate_video_svd
                if await ensure_comfyui_running(timeout=60):
                    path = await generate_video_svd(input_image="white_new.png")
                    if path:
                        return {"success": True, "path": path, "message": "视频生成成功（本地SVD）"}
                if mode == "local_svd":
                    return {"success": False, "message": "本地SVD生成失败"}

            if path:
                return {"success": True, "path": path, "message": "视频生成成功"}
            return {"success": False, "message": "视频生成失败"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ================================================================
    # QQ空间 API
    # ================================================================

    @router.get("/qzone/status")
    async def qzone_status() -> dict:
        """获取QQ空间配置状态。"""
        from white_salary.adapters.platform.qzone_api import get_client
        client = get_client()
        return {
            "configured": client.is_configured,
            "uin": client.uin if client.is_configured else "",
        }

    @router.post("/qzone/cookie")
    async def qzone_save_cookie(body: dict) -> dict:
        """保存QQ空间Cookie。"""
        uin = body.get("uin", "").strip()
        skey = body.get("skey", "").strip()
        p_skey = body.get("p_skey", "").strip()
        if not uin or not skey or not p_skey:
            return {"success": False, "message": "uin、skey、p_skey都不能为空"}
        from white_salary.adapters.platform.qzone_api import get_client
        client = get_client()
        client.save_config(uin, skey, p_skey)
        return {"success": True, "message": "QQ空间Cookie已保存"}

    @router.post("/qzone/test")
    async def qzone_test() -> dict:
        """测试QQ空间连接（获取最近说说）。"""
        from white_salary.adapters.platform.qzone_api import get_client
        client = get_client()
        if not client.is_configured:
            return {"success": False, "message": "未配置Cookie"}
        # 多试几次（QQ空间经常返回-10000"系统繁忙"）
        import asyncio
        for attempt in range(3):
            feeds = await client.get_feeds(1)
            if feeds:
                return {"success": True, "message": f"连接成功！最近说说: {feeds[0]['content'][:30]}"}
            await asyncio.sleep(1)
        return {"success": False, "message": "QQ空间返回系统繁忙(-10000)，Cookie可能正常，请稍后重试"}

    @router.post("/qzone/test-post")
    async def qzone_test_post(body: dict) -> dict:
        """
        2026-07-03 面板升级（批6）：发一条文字说说做发布测试。

        body: {"content": 说说文字}。调 qzone_api.post_emotion（纯文字模式）；
        Cookie 未配置/已过期/发布失败均返回明确错误
        （见 panel-qzone.json"发说说测试/监控设置"审计项）。
        """
        content = str(body.get("content") or "").strip()
        if not content:
            return {"success": False, "message": "content 不能为空"}
        if len(content) > 5000:
            return {"success": False, "message": "内容过长（最多5000字）"}
        from white_salary.adapters.platform import qzone_api as _qz
        client = _qz.get_client()
        if not client.is_configured:
            return {"success": False, "message": "未配置QQ空间Cookie，请先在本页登录或手动保存Cookie"}
        if getattr(client, "is_cookie_expired", False):
            return {"success": False, "message": "QQ空间Cookie已过期，请重新登录获取新Cookie"}
        result = await client.post_emotion(content)
        if result.get("success"):
            return {
                "success": True,
                "message": "说说发布成功",
                "tid": result.get("tid", ""),
            }
        return {
            "success": False,
            "message": f"发布失败: {result.get('error', '未知错误')}"
                       "（若提示登录相关错误，说明Cookie已失效，请重新登录）",
        }

    # ================================================================
    # 关于页 API
    # ================================================================

    @router.get("/about")
    async def get_about() -> dict:
        """
        2026-07-03 面板升级（批6）：关于页动态信息。

        返回 pyproject.toml 版本号 + 工具注册数 + 记忆模块统计 + Python 版本，
        替代 settings.html 里写死的过期快照（见 panel-about.json 审计项）。
        工具数需实例化 ToolRegistry（导入全部 builtin 工具，较重），
        放线程池并在首次成功后缓存。
        """
        import asyncio
        import platform
        import re as _re

        # 版本号：pyproject.toml 是单一来源（Python 3.10 无 tomllib，用正则提取）
        version = ""
        try:
            pyproject = project_root / "pyproject.toml"
            if pyproject.exists():
                m = _re.search(
                    r'^version\s*=\s*"([^"]+)"',
                    pyproject.read_text(encoding="utf-8"),
                    _re.M,
                )
                version = m.group(1) if m else ""
        except Exception as e:
            logger.warning(f"[About] 读取 pyproject 版本失败: {e}")

        # 工具注册数：首次调用实例化一次 ToolRegistry 后缓存（函数属性，进程内有效）
        tool_count = getattr(get_about, "_tool_count", None)
        if tool_count is None:
            def _count_tools() -> int:
                """同步实例化工具注册中心并取数量（导入较重，放线程池）。"""
                from white_salary.adapters.tools import registry as _registry_mod
                return _registry_mod.ToolRegistry().count
            try:
                tool_count = await asyncio.to_thread(_count_tools)
            except Exception as e:
                logger.warning(f"[About] 统计工具数失败: {e}")
                tool_count = 0
            get_about._tool_count = tool_count

        # 记忆模块统计：轻量文本探测 "MODULE =" 导出，不 import 任何模块
        module_total = 0
        module_stems: list[str] = []
        try:
            base = _memory_module_base
            for d in (base, base / "enhanced"):
                if not d.exists():
                    continue
                for f in sorted(d.glob("*.py")):
                    if f.name.startswith("_"):
                        continue
                    try:
                        if _re.search(r"^MODULE\s*=", f.read_text(encoding="utf-8"), _re.M):
                            module_total += 1
                            module_stems.append(f.stem)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"[About] 统计记忆模块失败: {e}")
        disabled_names = _read_disabled_modules()
        module_disabled = len([s for s in module_stems if s in disabled_names])

        return {
            "version": version,
            "python_version": platform.python_version(),
            "tool_count": tool_count,
            "module_total": module_total,
            "module_disabled": module_disabled,
            "module_enabled": module_total - module_disabled,
        }

    return router


def _deep_merge(base: dict, override: dict) -> None:
    """Deep merge override into base dict (in-place)."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _clean_masked_keys(new_config: dict, current_config: dict) -> dict:
    """
    If a value contains '***', it was masked — keep the original value.
    This prevents accidentally overwriting API keys with masked values.
    """
    cleaned = {}
    for k, v in new_config.items():
        if isinstance(v, dict):
            current_sub = current_config.get(k, {}) if isinstance(current_config.get(k), dict) else {}
            cleaned[k] = _clean_masked_keys(v, current_sub)
        elif isinstance(v, str) and "***" in v:
            # Keep original unmasked value
            original = current_config.get(k, "")
            cleaned[k] = original
        else:
            cleaned[k] = v
    return cleaned
