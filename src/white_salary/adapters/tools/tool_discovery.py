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
import time
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
    """本机所有可访问的固定盘根目录。

    **非 Windows 上返回空列表，不做目录扫描。** 本模块的全部识别特征都是
    Windows 专属的（`run_nvidia_gpu.bat`、`venv_new/Scripts/activate.bat`、
    `python_embeded/python.exe`、`start_cosyvoice.bat`），在 Linux/macOS 上
    结构性地永远不可能命中；而 comfyui_client 的自动启动本身也对非 Windows
    直接拒绝（"服务器环境请单独启动并配置 API 地址"）。

    所以在 Linux 上扫 `/` 纯属白干，还会把 /proc、/sys、/dev 这类伪文件系统
    扫进去——服务器上既慢又可能踩到奇怪的挂载点。ffmpeg 不受影响：它走
    `shutil.which()` 查 PATH，那条路在所有平台都有效。
    """
    if os.name != "nt":
        return []

    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                roots.append(root)
        except OSError:
            continue
    return roots


def _candidate_roots(extra: Iterable[Path] = ()) -> list[Path]:
    """候选起点：盘根 + 调用方补充的位置。

    **刻意不含下载/桌面/文档目录。** 这不是为了少扫几个地方，而是安全边界：

    探测出来的路径最终会被 `subprocess` 启动（ComfyUI 的 .bat、CosyVoice 的
    .bat）。把下载目录纳入扫描，等于"用户解压一个 zip 就可能让里面的批处理
    被自动执行"——攻击者只需在压缩包里放上 run_nvidia_gpu.bat + ComfyUI/ +
    python_embeded/python.exe，就能被认成"可运行的 ComfyUI"并在用户下次出图时
    被拉起。已实测复现。

    本改动前只有用户显式配置的路径会被执行，风险由用户自己掌握；探测把这个面
    扩大到了整块磁盘，因此必须把"未受信任内容的落地区"排除掉。真实的工具安装
    也不会待在下载目录里——用户会把它解压/移动到一个正式位置再用。

    用户确实想用非常规位置的工具时，仍可在 conf.yaml 的 external_tools 里显式
    指定（那是他自己的明确选择，优先级也最高）。
    """
    roots = list(extra)
    roots.extend(_fixed_drive_roots())
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


# 会被 cmd.exe 当作语法的字符。探测出来的路径最终会进
# `subprocess.Popen(..., shell=True)`，含这些字符的路径要么被截断、要么被当成
# 命令分隔符执行后半段。实测：`echo A & echo INJECTED` 会真的执行两条命令。
#
# 正规的工具安装目录不会带这些字符，所以这里直接拒绝而不是尝试转义——
# 自动探测是"替用户猜"，猜到可疑的东西就该退回"未配置"让用户自己指定，
# 而不是想办法把它跑起来。
_SHELL_UNSAFE_CHARS = frozenset('&|<>^"\'`$;\n\r\t')


def _is_shell_safe(path: Path) -> bool:
    """路径是否可以安全地交给 shell 启动。"""
    text = str(path)
    if any(char in _SHELL_UNSAFE_CHARS for char in text):
        return False
    # 百分号在 cmd 里会触发变量展开（%PATH% 之类），成对出现时尤其危险
    return text.count("%") < 2


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

    def _accept(directory: Path) -> None:
        if not _looks_like(directory, markers):
            return
        if not _is_shell_safe(directory):
            logger.warning(
                f"[ToolDiscovery] 跳过路径含 shell 特殊字符的 {label} 候选（不安全）: {directory}"
            )
            return
        candidates.append(directory)

    for parent in priority:
        _accept(parent)
        for directory in _iter_dirs(parent, max_depth=2):
            _accept(directory)

    # 第二轮：逐盘浅扫（深度 2），覆盖直接解压在盘根的情况
    for root in roots:
        for directory in _iter_dirs(root, max_depth=2):
            _accept(directory)

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

    # 可用性下限：择优只是候选之间比高低，还需要一个绝对门槛。
    #
    # 没有它的话，"只找到一份、而且这份跑不起来"会照样返回并落盘，接着
    # comfyui_client 真的去 Popen 那个必然失败的 .bat，轮询到超时才放弃——
    # 用户每次出图干等一分钟，还留下游离进程。误报（用户 clone 了源码仓库、
    # 留了个备份副本）正好落在这条路径上。
    #
    # 拿不到满分就当作"没找到"，让调用方按未配置处理并给出可操作提示。
    if runnable and _usability_score(best, runnable) < 2:
        logger.warning(
            f"[ToolDiscovery] 找到疑似 {label} 于 {best}，但缺少运行环境"
            f"（需要其中之一：{', '.join(runnable)}），按未配置处理。"
            "若这就是你要用的安装，请在 conf.yaml 的 external_tools 里显式指定。"
        )
        return None
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
        # marker 用硬件中立的 ComfyUI/main.py，不绑 run_nvidia_gpu.bat：
        # 后者会把 AMD 常用的 ZLUDA / DirectML 分发版整份漏掉（它们 bat 名字不同），
        # 同时让下面的 bat 选择逻辑失去意义（marker 保证了它必然存在）。
        "markers": ("ComfyUI/main.py",),
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
        # CosyVoice 与另外几个不同：它**不自带 python 运行环境**，
        # 启动脚本里自己写着用哪个解释器（实测真实安装是
        # `set PYTHON=<别处>\python_embeded\python.exe`，借用 ComfyUI 的内嵌 python）。
        # 所以"能不能跑"的判据就是"有没有启动脚本"，而不是目录里有没有 venv。
        #
        # 早先这里写的是 venv/Scripts/activate.bat 与 runtime/python.exe —— 那是我
        # 照另外几个工具的样子推测的，没有对照真实安装核对过。加上可用性下限后，
        # 真实安装立刻被判为"不可运行"而整个消失。教训：指纹必须拿真实安装验证，
        # 不能靠类比推断。
        "runnable": ("start_cosyvoice.bat", "start.bat", "run.bat", "webui.py"),
        # 权重目录名各版本不一，多列几个常见形态；都没有也不影响判定
        "content": ("pretrained_models", "asset", "examples"),
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

def autodetect_disabled() -> bool:
    """探测是否被显式关闭。

    **这是唯一权威判断，所有入口都必须查它。** 早先只在 external_paths 里查，
    于是 `warm_up_async()` 照样起线程扫全盘——用户/CI 明明关掉了探测，
    代价照付、结果却被请求路径那边的开关挡住用不上，两头亏。
    """
    return os.environ.get("WS_DISABLE_TOOL_AUTODETECT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


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


def _has_nvidia_gpu() -> bool:
    """本机是否有可用的 NVIDIA 显卡。

    判据是 nvidia-smi 是否存在（驱动装好就会带它，且会放进 System32）。
    比去 import torch 查 CUDA 便宜得多，也不需要拉起任何重依赖。
    """
    if shutil.which("nvidia-smi"):
        return True
    if os.name == "nt":
        system32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
        try:
            return (system32 / "nvidia-smi.exe").exists()
        except OSError:
            return False
    return False


def detect_comfyui_bat() -> Optional[Path]:
    """ComfyUI 启动脚本，按本机实际显卡挑。

    此前写成"优先 run_nvidia_gpu.bat，没有则退 run_cpu.bat"，但
    run_nvidia_gpu.bat 本身就在识别特征里，任何命中的目录必然有它 ——
    第二项永远走不到。于是非 NVIDIA 用户会被拉起 CUDA 版：进程直接退出，
    而 ensure_comfyui_running 要轮询到超时才放弃，**每次出图都白等一分钟**。

    另外官方文档明确 run_cpu.bat 只用于排障，不是 AMD 的正常选择，
    所以 AMD 相关分发版（ZLUDA / DirectML）的常见命名排在它前面。
    """
    directory = _discover("comfyui_dir", "ComfyUI")
    if not directory:
        return None

    if _has_nvidia_gpu():
        order = (
            "run_nvidia_gpu.bat",
            "run_nvidia_gpu_fast_fp16_accumulation.bat",
            "run_cpu.bat",
        )
    else:
        # 无 NVIDIA：绝不先试 CUDA 版。ZLUDA / DirectML 的常见命名优先，
        # run_cpu.bat 作为最后兜底（能出图，只是慢）。
        order = (
            "run_zluda.bat",
            "run_directml.bat",
            "run_amd.bat",
            "run_cpu.bat",
        )

    for name in order:
        candidate = directory / name
        if candidate.exists():
            if not _is_shell_safe(candidate):
                continue
            return candidate

    logger.debug(f"[ToolDiscovery] {directory} 下没有可用的启动脚本")
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
    if autodetect_disabled():
        return ""

    disk = _load_disk_cache()

    # 一、命中缓存（并验证路径仍然存在——用户可能挪走或删掉了工具）
    cached = disk.get(config_field, "")
    if cached:
        try:
            if Path(cached).exists():
                return cached
        except OSError:
            pass
        logger.info(f"[ToolDiscovery] 缓存的 {config_field} 已失效，将重新探测: {cached}")
        disk.pop(config_field, None)
        _cache.pop(_cache_key_for(config_field), None)
        _save_disk_cache()

    # 二、负缓存：上次扫过但没找到。
    #
    # 若不记这一笔，没装这些工具的用户**每次启动都要把所有固定盘重扫一遍**，
    # 而启动器那条链路是前台阻塞的（Start.bat 等 resolve 脚本输出），
    # 等于每次点启动白等十几秒还什么都没得到。
    # 记一个时间戳，过期后才允许重试——用户装好工具后不必手动清缓存。
    miss_key = f"__miss__{config_field}"
    last_miss = disk.get(miss_key, "")
    if last_miss and not _miss_expired(last_miss):
        return ""

    if not allow_scan:
        return ""

    # 三、真扫（结果落盘，后续启动不再付这个代价）
    try:
        found = detector()
    except Exception as exc:  # 探测失败绝不能影响主流程
        logger.debug(f"[ToolDiscovery] 探测 {config_field} 时出错（忽略）: {exc}")
        return ""

    if not found:
        disk[miss_key] = str(int(time.time()))
        _save_disk_cache()
        return ""

    disk.pop(miss_key, None)
    disk[config_field] = str(found)
    _save_disk_cache()
    return str(found)


# 负缓存有效期：没找到的结论只信这么久，之后允许重扫。
# 取一天——用户今天装好工具，明天最迟也能被自动发现，而不必知道有个缓存文件要删。
# 想立刻生效可以在设置面板点"重新扫描"（rescan）。
_MISS_TTL_SECONDS = 24 * 3600


def _miss_expired(stamp: str) -> bool:
    try:
        return (time.time() - float(stamp)) > _MISS_TTL_SECONDS
    except (TypeError, ValueError):
        return True


def _cache_key_for(config_field: str) -> str:
    """配置字段 → 内存缓存键（多个字段可能共享同一次目录探测）。"""
    if config_field.startswith("comfyui"):
        return "comfyui_dir"
    if config_field.startswith("cosyvoice"):
        return "cosyvoice_dir"
    if config_field.startswith("ffmpeg"):
        return "ffmpeg"
    return config_field


def detect_all(*, allow_scan: bool = True) -> dict[str, str]:
    """探测全部外部工具，供安装向导/设置面板/启动预热一次性填充。"""
    return {field: detect(field, allow_scan=allow_scan) for field in _DETECTORS}


def warm_up_async() -> None:
    """后台线程预热探测缓存。

    在服务启动时调一次即可：把那 15 秒的冷扫描放到后台，等真正要用路径时
    缓存通常已经就绪；即使没就绪，请求路径走 allow_scan=False 也不会被卡住。
    """
    if autodetect_disabled():
        logger.debug("[ToolDiscovery] 探测已被 WS_DISABLE_TOOL_AUTODETECT 关闭，跳过预热")
        return

    import threading

    def _run() -> None:
        try:
            detect_all(allow_scan=True)
        except Exception as exc:  # pragma: no cover - 后台任务不许影响主流程
            logger.debug(f"[ToolDiscovery] 后台预热失败（忽略）: {exc}")

    thread = threading.Thread(target=_run, name="tool-discovery-warmup", daemon=True)
    thread.start()


def rescan() -> dict[str, str]:
    """强制重新扫描并覆盖缓存（设置面板的"重新扫描"按钮用）。

    注意也受总开关约束：显式关闭探测时不做任何事，避免"关了却还在扫盘"。
    """
    if autodetect_disabled():
        logger.info("[ToolDiscovery] 探测已被显式关闭，重新扫描请求被忽略")
        return {}
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
