r"""外部工具自动探测 —— 让用户装好就能点启动，而不是先去配文件。

## 为什么需要它

外部工具（GPT-SoVITS / ComfyUI / CosyVoice / Wav2Lip / ffmpeg）原本的解析链路是
`环境变量 → conf.yaml 的 external_tools → 内置默认值`。公开版的内置默认值必须为空
（不能夹带作者机器的路径），于是**新用户装好后点启动，解析直接失败**：TTS 拉不起来、
出图不可用，而用户什么都没做错。他要么去翻文档手写路径，要么就放弃了。

这一层补的就是中间那一步：**自己去找**。

## 设计取舍

1. **按特征文件识别，不按目录名猜。** 用户可能把 GPT-SoVITS 解压成
   `D:\语音合成`、`E:\tools\sovits-v2`，靠名字匹配必然漏。判据改成"这个目录里
   同时存在 api_v2.py 和 GPT_SoVITS/"——这是它的指纹，改名也认得出。
2. **有界扫描，不遍历全盘。** 只看各固定盘根目录及常见工具父目录，深度上限 3 层，
   并跳过 Windows/ProgramData/AppData/node_modules 这类必然无关且巨大的目录。
   全盘 walk 在机械硬盘上要几分钟，那还不如报错。
3. **注册表用不上。** 这些工具都是绿色便携版（下载解压即用），不会写注册表；
   只有 ffmpeg 这类可执行文件适合先走 `shutil.which()` 查 PATH。
4. **只做兜底，不抢配置。** 顺序仍是 环境变量 → conf.yaml → **自动探测** → 空。
   用户显式配了什么就用什么，探测只在没配时出手。
5. **进程内缓存。** 扫描结果缓存，避免每次取路径都重扫。

## 这不会把作者的私人路径带进公开仓库

探测规则里没有任何具体路径，只有"哪些盘符""哪些常见父目录名""什么特征文件"。
它在别人的机器上找别人的目录，在作者机器上找作者的目录。
"""

from __future__ import annotations

import os
import shutil
import string
from pathlib import Path
from typing import Callable, Iterable, Optional

from loguru import logger

# 扫描深度上限（相对候选根目录）。3 层足够覆盖
# `<盘>\<工具集目录>\<工具>` 与 `<盘>\<工具集目录>\<分类>\<工具>` 这两种常见布局。
_MAX_DEPTH = 3

# 这些目录要么与外部工具无关、要么极其巨大，扫进去只会拖慢启动
_SKIP_DIR_NAMES = {
    "windows", "$recycle.bin", "system volume information", "programdata",
    "appdata", "node_modules", ".git", "__pycache__", ".venv", "venv",
    "site-packages", "temp", "tmp", "cache", ".cache", "onedrive",
    "recovery", "perflogs", "msocache", "intel", "nvidia corporation",
}

# 常见的"工具集中放置"父目录名，命中后会被优先深挖
_LIKELY_PARENT_NAMES = {
    "ai", "ai_tools", "aitools", "tools", "apps", "programs", "soft",
    "software", "green", "portable", "models", "sd", "work",
}


def _fixed_drive_roots() -> list[Path]:
    """本机所有可访问的固定盘根目录。"""
    roots: list[Path] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:/")
            try:
                if root.exists():
                    roots.append(root)
            except OSError:
                continue
    else:
        roots.append(Path("/"))
    return roots


def _candidate_roots(extra: Iterable[Path] = ()) -> list[Path]:
    """候选起点：盘根 + 用户目录 + 调用方补充的位置。"""
    roots = list(extra)
    roots.extend(_fixed_drive_roots())
    home = Path.home()
    for name in ("Desktop", "Downloads", "Documents", "桌面", "下载", "文档"):
        candidate = home / name
        try:
            if candidate.is_dir():
                roots.append(candidate)
        except OSError:
            continue
    # 去重且保持顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _iter_dirs(root: Path, max_depth: int = _MAX_DEPTH):
    """从 root 开始按层遍历目录，带剪枝与深度上限。"""
    frontier = [(root, 0)]
    while frontier:
        current, depth = frontier.pop()
        if depth > max_depth:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    name_lower = entry.name.lower()
                    if name_lower in _SKIP_DIR_NAMES or name_lower.startswith("$"):
                        continue
                    path = Path(entry.path)
                    yield path
                    if depth < max_depth:
                        frontier.append((path, depth + 1))
        except (PermissionError, OSError):
            continue


def _looks_like(directory: Path, markers: tuple[str, ...]) -> bool:
    """目录内是否同时存在全部特征项（文件或子目录都算）。"""
    try:
        for marker in markers:
            if not (directory / marker).exists():
                return False
        return True
    except OSError:
        return False


def _usability_score(directory: Path, runnable: tuple[str, ...]) -> int:
    """候选目录的"能不能真跑起来"评分。

    这一层是实测逼出来的：同一台机器上很可能存在同一个工具的多份副本
    （一份下载解压后没装依赖，另一份才是日常在用的）。只按特征文件匹配的话，
    先扫到哪个就用哪个——如果恰好是没装运行环境的那份，用户点启动就会失败，
    而且现象是"没反应"，极难自查。

    所以命中特征只算入围，还要看它是否具备真正启动所需的运行环境
    （GPT-SoVITS 的 venv_new、ComfyUI 便携版的 python_embeded 等）。
    """
    if not runnable:
        return 1
    for marker in runnable:
        try:
            if (directory / marker).exists():
                return 2
        except OSError:
            continue
    return 1


def _content_score(directory: Path, content_dirs: tuple[str, ...], cap: int = 200) -> int:
    """候选目录里"用户自己攒的内容"有多少（模型、权重、LoRA 等）。

    多份副本在"能跑"上打平时，装了更多模型的那份显然才是日常在用的——
    另一份多半是当初解压完就放着没管。这个信号是通用的，不依赖任何具体路径。
    计数带上限，避免在超大目录上浪费时间。
    """
    total = 0
    for name in content_dirs:
        try:
            target = directory / name
            if not target.is_dir():
                continue
            with os.scandir(target) as entries:
                for _ in entries:
                    total += 1
                    if total >= cap:
                        return total
        except OSError:
            continue
    return total


def find_tool_dir(
    markers: tuple[str, ...],
    *,
    label: str,
    runnable: tuple[str, ...] = (),
    content_dirs: tuple[str, ...] = (),
    extra_roots: Iterable[Path] = (),
    search_roots: Optional[Iterable[Path]] = None,
) -> Optional[Path]:
    """按特征文件找出工具所在目录，找不到返回 None。

    收集全部命中项后按"可用性"择优，而不是取第一个——同一工具存在多份副本时，
    必须选那份真能启动的（见 _usability_score）。

    Args:
        extra_roots:  在默认候选起点之外**追加**的搜索位置
        search_roots: 只搜这些位置（给定时完全取代默认候选起点）。
            用于"只扫用户指定的一个文件夹"这类场景，测试也靠它保持可复现——
            否则用例会扫到真实机器上的安装，结果随机器而变。
    """
    roots = (
        [Path(r) for r in search_roots]
        if search_roots is not None
        else _candidate_roots(extra_roots)
    )
    candidates: list[Path] = []

    # 第一轮：只看盘根下那些"一看就是放工具的"目录，命中率高且极快
    priority: list[Path] = []
    for root in roots:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False) and (
                            entry.name.lower() in _LIKELY_PARENT_NAMES
                        ):
                            priority.append(Path(entry.path))
                    except OSError:
                        continue
        except (PermissionError, OSError):
            continue

    for parent in priority:
        if _looks_like(parent, markers):
            candidates.append(parent)
        for directory in _iter_dirs(parent, max_depth=2):
            if _looks_like(directory, markers):
                candidates.append(directory)

    # 第二轮：逐盘浅扫（深度 2），覆盖直接解压在盘根的情况
    for root in roots:
        for directory in _iter_dirs(root, max_depth=2):
            if _looks_like(directory, markers):
                candidates.append(directory)

    if not candidates:
        logger.debug(f"[ToolDiscovery] 未能自动找到 {label}")
        return None

    # 去重后择优：可用性高的优先；同分时取路径较短的（通常是更"正式"的安装位置）
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)

    # 择优顺序：能不能跑 > 用户攒的内容多少 > 路径更短（更像正式安装位置）
    best = max(
        unique,
        key=lambda p: (
            _usability_score(p, runnable),
            _content_score(p, content_dirs),
            -len(str(p)),
        ),
    )
    if len(unique) > 1:
        others = [str(p) for p in unique if p != best]
        logger.info(
            f"[ToolDiscovery] {label} 发现 {len(unique)} 份，选用可运行的: {best}"
            f"（其余: {', '.join(others[:3])}）"
        )
    return best


# ---------------------------------------------------------------------------
# 各工具的指纹
#
# 判据一律取"改名也不会变"的内部结构，而不是目录名。
# ---------------------------------------------------------------------------

# markers   = 认出"这是该工具"的指纹（改目录名也认得出）
# runnable  = 认出"这份能真跑起来"的运行环境标志（多份副本时据此择优）
_SIGNATURES: dict[str, dict[str, tuple[str, ...]]] = {
    # GPT-SoVITS：API 入口脚本 + 核心包目录；启动脚本要 venv_new 里的解释器
    "gpt_sovits_dir": {
        "markers": ("api_v2.py", "GPT_SoVITS"),
        "runnable": ("venv_new/Scripts/activate.bat", "runtime/python.exe"),
        "content": ("GPT_weights", "SoVITS_weights", "GPT_weights_v2", "SoVITS_weights_v2"),
    },
    # ComfyUI 便携版：启动脚本 + 主程序目录；便携版靠内嵌 python 运行
    "comfyui_dir": {
        "markers": ("run_nvidia_gpu.bat", "ComfyUI"),
        "runnable": ("python_embeded/python.exe",),
        # 只数"用户刻意装进去的模型"，不数 output。
        # 实测教训：某份副本积了 892 张输出图，仅凭 output 就把计数上限撑满，
        # 盖过了另一份多装 7 个 LoRA 的真实差异。生成产物只说明"用过一阵"，
        # 装了多少模型才代表"这份是主力配置"。
        "content": ("ComfyUI/models/checkpoints", "ComfyUI/models/loras"),
    },
    # CosyVoice：核心包 + API 服务入口
    "cosyvoice_dir": {
        "markers": ("cosyvoice", "api_server.py"),
        "runnable": ("venv/Scripts/activate.bat", "runtime/python.exe"),
        "content": ("pretrained_models",),
    },
    # Wav2Lip：超参文件 + 权重目录；没有权重就跑不出结果
    "wav2lip_dir": {
        "markers": ("hparams.py", "checkpoints"),
        "runnable": ("checkpoints/wav2lip_gan.pth", "checkpoints/wav2lip.pth"),
        "content": ("checkpoints",),
    },
}

_cache: dict[str, Optional[Path]] = {}

# ---------------------------------------------------------------------------
# 磁盘缓存
#
# 为什么必须有：本机实测**冷缓存下单次探测要 15 秒**（热缓存只要 0.8 秒，
# 所以开发时很容易被"看起来很快"骗过去）。而这是同步扫盘：
#   - 启动器每次点启动都要重新付一遍这个代价；
#   - 更糟的是若它发生在请求处理路径上，会把整个事件循环卡住十几秒，
#     用户看到的就是"点了没反应"。
#
# 因此结果落盘：只有第一次（或路径失效时）需要真扫。缓存里的路径每次使用前
# 都验证是否仍然存在——用户挪动或删除工具后不能让陈旧路径把启动带进坑里。
# ---------------------------------------------------------------------------

_CACHE_VERSION = 1
_disk_cache_path: Optional[Path] = None
_disk_cache_loaded = False
_disk_cache: dict[str, str] = {}


def configure_cache_path(path: Path | str) -> None:
    """设置探测结果缓存文件位置（一般是 <项目>/data/tool_paths.json）。"""
    global _disk_cache_path, _disk_cache_loaded, _disk_cache
    _disk_cache_path = Path(path)
    _disk_cache_loaded = False
    _disk_cache = {}


def _default_cache_path() -> Path:
    # 默认落在项目 data/ 下；这里不引入 config 依赖，避免循环导入。
    return Path(__file__).resolve().parents[4] / "data" / "tool_paths.json"


def _load_disk_cache() -> dict[str, str]:
    global _disk_cache_loaded, _disk_cache
    if _disk_cache_loaded:
        return _disk_cache
    _disk_cache_loaded = True
    path = _disk_cache_path or _default_cache_path()
    try:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        if int(raw.get("version", 0)) != _CACHE_VERSION:
            _disk_cache = {}
        else:
            entries = raw.get("paths", {})
            _disk_cache = {str(k): str(v) for k, v in entries.items() if v}
    except Exception:
        _disk_cache = {}
    return _disk_cache


def _save_disk_cache() -> None:
    path = _disk_cache_path or _default_cache_path()
    try:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _CACHE_VERSION, "paths": _disk_cache}
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        logger.debug(f"[ToolDiscovery] 探测缓存写入失败（不影响功能）: {exc}")


def _discover(key: str, label: str) -> Optional[Path]:
    if key in _cache:
        return _cache[key]
    spec = _SIGNATURES[key]
    found = find_tool_dir(
        spec["markers"],
        label=label,
        runnable=spec.get("runnable", ()),
        content_dirs=spec.get("content", ()),
    )
    if found:
        logger.info(f"[ToolDiscovery] 自动找到 {label}: {found}")
    _cache[key] = found
    return found


def detect_gpt_sovits_dir() -> Optional[Path]:
    return _discover("gpt_sovits_dir", "GPT-SoVITS")


def detect_comfyui_bat() -> Optional[Path]:
    """ComfyUI 启动脚本。优先 NVIDIA 版，没有则退 CPU 版。"""
    directory = _discover("comfyui_dir", "ComfyUI")
    if not directory:
        return None
    for name in ("run_nvidia_gpu.bat", "run_cpu.bat"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def detect_comfyui_input() -> Optional[Path]:
    directory = _discover("comfyui_dir", "ComfyUI")
    if not directory:
        return None
    candidate = directory / "ComfyUI" / "input"
    return candidate if candidate.exists() else None


def detect_cosyvoice_bat() -> Optional[Path]:
    directory = _discover("cosyvoice_dir", "CosyVoice")
    if not directory:
        return None
    for name in ("start_cosyvoice.bat", "start.bat", "run.bat"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def detect_wav2lip_dir() -> Optional[Path]:
    return _discover("wav2lip_dir", "Wav2Lip")


def detect_ffmpeg() -> Optional[Path]:
    """ffmpeg 是普通可执行文件，先查 PATH 再扫常见位置。"""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)

    found = find_tool_dir(("bin/ffmpeg.exe",) if os.name == "nt" else ("bin/ffmpeg",),
                          label="ffmpeg")
    if found:
        binary = found / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if binary.exists():
            return binary
    return None


_DETECTORS: dict[str, Callable[[], Optional[Path]]] = {
    "gpt_sovits_dir": detect_gpt_sovits_dir,
    "comfyui_bat": detect_comfyui_bat,
    "comfyui_input": detect_comfyui_input,
    "cosyvoice_bat": detect_cosyvoice_bat,
    "wav2lip_dir": detect_wav2lip_dir,
    "ffmpeg_path": detect_ffmpeg,
}


def detect(config_field: str, *, allow_scan: bool = True) -> str:
    """按配置字段名解析外部工具路径，找不到返回空串。

    Args:
        allow_scan: 允许在缓存未命中时真的扫盘。**请求处理路径必须传 False**——
            冷缓存下一次扫描实测要 15 秒，同步执行会把事件循环整个卡住，
            用户看到的就是"点了没反应"。传 False 时只查缓存，未命中就返回空。
    """
    detector = _DETECTORS.get(config_field)
    if detector is None:
        return ""

    # 一、磁盘缓存（并验证路径仍然存在——用户可能挪走或删掉了工具）
    cached = _load_disk_cache().get(config_field, "")
    if cached:
        try:
            if Path(cached).exists():
                return cached
        except OSError:
            pass
        logger.info(f"[ToolDiscovery] 缓存的 {config_field} 已失效，将重新探测: {cached}")
        _disk_cache.pop(config_field, None)
        _cache.clear()
        _save_disk_cache()

    if not allow_scan:
        return ""

    # 二、真扫（结果落盘，后续启动不再付这个代价）
    try:
        found = detector()
    except Exception as exc:  # 探测失败绝不能影响主流程
        logger.debug(f"[ToolDiscovery] 探测 {config_field} 时出错（忽略）: {exc}")
        return ""

    if not found:
        return ""

    _load_disk_cache()[config_field] = str(found)
    _save_disk_cache()
    return str(found)


def detect_all(*, allow_scan: bool = True) -> dict[str, str]:
    """探测全部外部工具，供安装向导/设置面板/启动预热一次性填充。"""
    return {field: detect(field, allow_scan=allow_scan) for field in _DETECTORS}


def warm_up_async() -> None:
    """后台线程预热探测缓存。

    在服务启动时调一次即可：把那 15 秒的冷扫描放到后台，等真正要用路径时
    缓存通常已经就绪；即使没就绪，请求路径走 allow_scan=False 也不会被卡住。
    """
    import threading

    def _run() -> None:
        try:
            detect_all(allow_scan=True)
        except Exception as exc:  # pragma: no cover - 后台任务不许影响主流程
            logger.debug(f"[ToolDiscovery] 后台预热失败（忽略）: {exc}")

    thread = threading.Thread(target=_run, name="tool-discovery-warmup", daemon=True)
    thread.start()


def rescan() -> dict[str, str]:
    """强制重新扫描并覆盖缓存（设置面板的"重新扫描"按钮用）。"""
    clear_cache(clear_disk=True)
    return detect_all(allow_scan=True)


def clear_cache(*, clear_disk: bool = False) -> None:
    """清空探测缓存（测试与设置面板"重新扫描"用）。"""
    global _disk_cache_loaded, _disk_cache
    _cache.clear()
    if clear_disk:
        _disk_cache = {}
        _disk_cache_loaded = True
        _save_disk_cache()
