"""
white_salary/adapters/tools/external_paths.py

2026-07-03 外部依赖优化（批8）：外部本地工具路径的统一解析入口。

背景：ComfyUI / CosyVoice / GPT-SoVITS / Wav2Lip / ffmpeg 等"本地进阶功能"的
安装路径此前散落在各 adapter 里硬编码（换机器就得改源码），且部分模块在 import
期就读死路径。本模块把这些路径收拢到一处，提供统一的解析顺序：

    环境变量(保留各处历史 WS_* 变量) → conf.yaml 的 external_tools 节

用户方向：云端为主（填 API key 就能用）、本地大模型作进阶可选。因此这些本地路径
默认不再携带作者机器的固定路径；只用云端的用户可以完全忽略。进阶用户想接入本地
工具时，改 conf.yaml 的 external_tools 节即可，无需改源码、也无需设一堆环境变量。

设计要点：
  1. 惰性读配置——本模块不在 import 期加载配置，首次调用解析函数时才 load_config
     并缓存（避免各 adapter 在 import 期就触发配置加载 / 循环依赖）；
  2. 外部工具未配置时由调用方清晰降级或提示，不回退到作者机器路径；
  3. 配置读取失败（例如脱离项目根跑单测）时静默视为未配置，不抛异常。
"""

import os
from pathlib import Path
from typing import Optional

from loguru import logger

# =============================================================================
# 内置默认值（保持为空，避免作者机器路径污染部署）
# =============================================================================

# ComfyUI 启动脚本
DEFAULT_COMFYUI_BAT: str = ""
# ComfyUI 的 input 目录
DEFAULT_COMFYUI_INPUT: str = ""
# GPT-SoVITS 安装目录
DEFAULT_GPT_SOVITS_DIR: str = ""
# CosyVoice 启动脚本
DEFAULT_COSYVOICE_BAT: str = ""
# Wav2Lip 安装目录
DEFAULT_WAV2LIP_DIR: str = ""
# ffmpeg 候选路径
DEFAULT_FFMPEG_PATHS: tuple[str, ...] = (
)


# =============================================================================
# external_tools 配置的惰性缓存
# =============================================================================

# _ExternalToolsConfig 单例缓存；None = 尚未尝试加载
_cached_external_tools: Optional[object] = None
_load_attempted: bool = False


def _get_external_tools_config() -> Optional[object]:
    """
    惰性读取合并后配置里的 external_tools 节（ExternalToolsConfig）。

    首次调用时 load_config() 并缓存结果；加载失败（脱离项目根、配置损坏等）
    时缓存 None 并只告警一次，后续调用直接走内置默认值，绝不抛异常打断主流程。

    Returns:
        ExternalToolsConfig 实例；不可用时返回 None
    """
    global _cached_external_tools, _load_attempted
    if _load_attempted:
        return _cached_external_tools

    _load_attempted = True
    try:
        # 延迟到此处 import，避免 adapter import 期触发配置模块加载
        from white_salary.infrastructure.config import load_config

        config = load_config()
        _cached_external_tools = getattr(config, "external_tools", None)
    except Exception as exc:  # 配置不可用时回退默认值，不影响云端主流程
        logger.debug(f"[ExternalPaths] 读取 external_tools 配置失败，回退内置默认值: {exc}")
        _cached_external_tools = None
    return _cached_external_tools


def reset_cache() -> None:
    """
    清空 external_tools 配置缓存（仅供单测在 monkeypatch 配置后重置用）。
    """
    global _cached_external_tools, _load_attempted
    _cached_external_tools = None
    _load_attempted = False


def _config_value(field: str) -> str:
    """
    从 external_tools 配置里取某字段（非空字符串才算"用户配置了"）。

    Args:
        field: ExternalToolsConfig 的字段名

    Returns:
        配置的路径字符串；未配置 / 配置为空 / 配置不可用时返回空串
    """
    cfg = _get_external_tools_config()
    if cfg is None:
        return ""
    value = getattr(cfg, field, "")
    return value.strip() if isinstance(value, str) else ""


def _resolve(env_var: str, config_field: str, default: str) -> str:
    """
    按统一三级顺序解析一条外部工具路径。

    顺序：环境变量(env_var) → external_tools 配置(config_field) → 内置默认值(default)。
    环境变量与配置都以"非空"为准（空串视为未设置，继续向下回退）。

    Args:
        env_var:      历史环境变量名（保留各处旧行为，最高优先级）
        config_field: ExternalToolsConfig 上对应字段名
        default:      内置默认值（== 历史硬编码值）

    Returns:
        解析后的路径字符串
    """
    env_value = os.environ.get(env_var, "").strip()
    if env_value:
        return env_value
    cfg_value = _config_value(config_field)
    if cfg_value:
        return cfg_value
    return default


# =============================================================================
# 对外的路径解析函数（各 adapter 调用）
# =============================================================================

def get_comfyui_bat() -> Path:
    """ComfyUI 启动脚本路径（环境变量 WS_COMFYUI_BAT → 配置）。"""
    value = _resolve("WS_COMFYUI_BAT", "comfyui_bat", DEFAULT_COMFYUI_BAT)
    if not value:
        raise FileNotFoundError(
            "ComfyUI start script is not configured. Set external_tools.comfyui_bat "
            "in conf.yaml or WS_COMFYUI_BAT."
        )
    return Path(value)


def get_comfyui_input() -> Path:
    """ComfyUI input 目录（环境变量 WS_COMFYUI_INPUT → 配置）。"""
    value = _resolve("WS_COMFYUI_INPUT", "comfyui_input", DEFAULT_COMFYUI_INPUT)
    if not value:
        raise FileNotFoundError(
            "ComfyUI input directory is not configured. Set external_tools.comfyui_input "
            "in conf.yaml or WS_COMFYUI_INPUT."
        )
    return Path(value)


def get_gpt_sovits_dir() -> Path:
    """GPT-SoVITS 安装目录（环境变量 WS_GPT_SOVITS_DIR → 配置）。"""
    value = _resolve("WS_GPT_SOVITS_DIR", "gpt_sovits_dir", DEFAULT_GPT_SOVITS_DIR)
    if not value:
        raise FileNotFoundError(
            "GPT-SoVITS path is not configured. Set external_tools.gpt_sovits_dir "
            "in conf.yaml or WS_GPT_SOVITS_DIR."
        )
    return Path(value)


def get_cosyvoice_bat() -> Path:
    """CosyVoice 启动脚本路径（环境变量 WS_COSYVOICE_BAT → 配置）。"""
    value = _resolve("WS_COSYVOICE_BAT", "cosyvoice_bat", DEFAULT_COSYVOICE_BAT)
    if not value:
        raise FileNotFoundError(
            "CosyVoice start script is not configured. Set external_tools.cosyvoice_bat "
            "in conf.yaml or WS_COSYVOICE_BAT."
        )
    return Path(value)


def get_wav2lip_dir() -> Path:
    """Wav2Lip 安装目录（环境变量 WS_WAV2LIP_DIR → 配置）。"""
    value = _resolve("WS_WAV2LIP_DIR", "wav2lip_dir", DEFAULT_WAV2LIP_DIR)
    if not value:
        raise FileNotFoundError(
            "Wav2Lip directory is not configured. Set external_tools.wav2lip_dir "
            "in conf.yaml or WS_WAV2LIP_DIR."
        )
    return Path(value)


def find_ffmpeg(prefer_path_first: bool = False) -> Optional[str]:
    """
    查找可用的 ffmpeg 可执行文件。

    环境变量 WS_FFMPEG_PATH 与 external_tools.ffmpeg_path 配置永远最优先（若指向
    存在的文件）；两者都没有时，PATH 与内置候选路径的先后顺序由 prefer_path_first
    决定。内置候选路径默认为空；不再携带作者机器路径：

      - audio_convert.find_ffmpeg 旧顺序：PATH → 内置候选  → 传 prefer_path_first=True
      - video_gen._find_ffmpeg   旧顺序：内置候选 → PATH   → 传 prefer_path_first=False（默认）

    Args:
        prefer_path_first: True=先查 PATH 再查内置候选；False=先查内置候选再查 PATH。

    Returns:
        ffmpeg 完整路径；全都找不到返回 None（调用方降级处理）。
    """
    import shutil

    # 环境变量 / 配置显式指定的路径最优先。
    env_ff = os.environ.get("WS_FFMPEG_PATH", "").strip()
    if env_ff and Path(env_ff).exists():
        return env_ff
    cfg_ff = _config_value("ffmpeg_path")
    if cfg_ff and Path(cfg_ff).exists():
        return cfg_ff

    def _from_path() -> Optional[str]:
        return shutil.which("ffmpeg")

    def _from_candidates() -> Optional[str]:
        for candidate in DEFAULT_FFMPEG_PATHS:
            if Path(candidate).exists():
                return candidate
        return None

    lookups = (_from_path, _from_candidates) if prefer_path_first else (_from_candidates, _from_path)
    for lookup in lookups:
        found = lookup()
        if found:
            return found
    return None
