"""
White Salary - 首次运行自检脚本（2026-07-03 开源准备（批7）：新增独立工具，不改任何现有代码）

作用：别人 clone 项目后，运行这一个脚本就能知道"环境配好了没、还差什么、下一步做什么"。
它会逐项检查并用中文友好提示：
  1. Python 版本是否达标（>=3.10）
  2. conf.yaml 是否存在（不存在提示从 conf.default.yaml 复制）
  3. 主 LLM 密钥是否填了（最少能桌面聊天的必要条件）
  4. 关键依赖是否装了（import 探测 fastapi / pydantic / ddgs / yt_dlp 等）
  5. 前端依赖是否装了（frontend/node_modules）
  6. data/ 目录结构是否就绪
最后给出"下一步做什么"的建议。

用法：
    python scripts/first_run_check.py

本脚本只读不写，不联网、不启动服务器，纯粹是"体检"。
"""

import importlib.util
import sys
from pathlib import Path

# 项目根目录 = 本脚本的上一级的上一级（scripts/ 的父目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# 终端上色（Windows 现代终端普遍支持 ANSI；不支持时最多显示几个转义字符，无害）
# =============================================================================
class C:
    """ANSI 颜色代码的极简封装。"""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def ok(msg: str) -> None:
    """打印一条"通过"提示（绿色对勾）。"""
    print(f"  {C.GREEN}[OK]{C.RESET}   {msg}")


def warn(msg: str) -> None:
    """打印一条"警告/可选缺失"提示（黄色）。"""
    print(f"  {C.YELLOW}[!]{C.RESET}    {msg}")


def fail(msg: str) -> None:
    """打印一条"必须修复"提示（红色）。"""
    print(f"  {C.RED}[X]{C.RESET}    {msg}")


def title(msg: str) -> None:
    """打印一个分节标题。"""
    print(f"\n{C.BOLD}{C.CYAN}{msg}{C.RESET}")


# =============================================================================
# 各项检查——每个函数返回 True=通过 / False=有硬性问题（会计入"必须修复"计数）
# =============================================================================
def check_python() -> bool:
    """
    检查 Python 版本。

    说明：pyproject.toml 声明 requires-python >=3.10；低于 3.10 直接判失败。
    3.10+ 可以运行，建议有条件时使用 3.11+。
    """
    title("1. Python 版本")
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) >= (3, 10):
        ok(f"Python {version_str}（满足 pyproject 声明的 >=3.10；建议 3.11+）。")
        return True
    fail(f"Python {version_str} 版本过低——请使用 3.10 及以上（建议 3.11+）。")
    return False


def _load_conf() -> tuple[dict, str]:
    """
    尝试读取并解析 conf.yaml。

    返回:
        (配置字典, 错误说明)。成功时错误说明为空串；
        文件不存在或解析失败时返回 ({}, 原因)。
    """
    conf_path = PROJECT_ROOT / "conf.yaml"
    if not conf_path.exists():
        return {}, "not_found"
    try:
        import yaml
    except ImportError:
        # PyYAML 还没装（属于必需依赖，会在依赖检查里另行报出），这里先跳过解析
        return {}, "no_yaml"
    try:
        data = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}, "empty"
        return data, ""
    except Exception as exc:  # noqa: BLE001 —— 这里要把任何 YAML 语法错误友好地转述给用户
        return {}, f"parse_error: {exc}"


def check_conf() -> bool:
    """检查 conf.yaml 是否存在、能解析、主 LLM 密钥是否填了。"""
    title("2. 配置文件 conf.yaml")
    conf_path = PROJECT_ROOT / "conf.yaml"
    default_path = PROJECT_ROOT / "conf.default.yaml"

    if not conf_path.exists():
        fail("conf.yaml 不存在。")
        if default_path.exists():
            print(f"       请先复制模板：{C.BOLD}copy conf.default.yaml conf.yaml{C.RESET}"
                  f"（Git Bash 用 cp），再填入你的 LLM 密钥。")
        else:
            fail("       连 conf.default.yaml 模板也找不到——请确认你在项目根目录下运行。")
        return False

    ok("conf.yaml 已存在。")

    conf, err = _load_conf()
    if err == "no_yaml":
        warn("PyYAML 未安装，暂时无法解析 conf.yaml 内容（依赖检查会提示安装）。")
        return True  # 文件在就算通过；密钥检查等装好 yaml 再说
    if err == "empty":
        fail("conf.yaml 是空的或格式不对——请参照 conf.default.yaml 至少填好 llm 节。")
        return False
    if err.startswith("parse_error"):
        fail(f"conf.yaml 解析失败（YAML 语法错误）：{err[len('parse_error: '):]}")
        return False

    # 主 LLM 密钥检查——这是"最少能聊天"的硬性条件
    llm = conf.get("llm") or {}
    api_key = str(llm.get("api_key") or "").strip()
    if api_key:
        provider = llm.get("provider") or "（未写 provider）"
        model = llm.get("model") or "（未写 model）"
        ok(f"主 LLM 密钥已填写（provider={provider}，model={model}）。")
        _report_role_llms(conf)
        return True

    fail("主 LLM 密钥（llm.api_key）还是空的——这是桌面聊天的必要条件。")
    print("       请编辑 conf.yaml 的 llm 节，填入 api_key / provider / model / base_url。")
    print("       详见 docs/CONFIG.md 的「LLM 多角色架构」一节。")
    return False


def _report_role_llms(conf: dict) -> None:
    """附带报告 7 个分角色 LLM 填了几个（不影响主聊天，仅提示增强度）。"""
    roles = [
        ("llm_tool", "工具判断"),
        ("llm_memory", "记忆分析"),
        ("llm_emotion", "情感分析"),
        ("llm_vision", "视觉理解"),
        ("llm_postprocess", "后处理"),
        ("llm_detect", "检测防护"),
        ("llm_background", "后台任务"),
    ]
    filled = [name for key, name in roles
              if str((conf.get(key) or {}).get("api_key") or "").strip()]
    if len(filled) == len(roles):
        ok(f"7 个分角色 LLM 全部已配置（记忆/情感/看图等增强功能可用）。")
    elif filled:
        warn(f"分角色 LLM 已配 {len(filled)}/{len(roles)}（已配：{'、'.join(filled)}）。"
             f"未配的角色对应功能会降级，不影响主聊天。")
    else:
        warn("7 个分角色 LLM 都没配——记忆提取/情感分析/看图等增强功能会关闭，"
             "但桌面主聊天正常。想全开可逐个填，见 docs/CONFIG.md。")


def check_dependencies() -> bool:
    """import 探测关键 Python 依赖是否装齐。"""
    title("3. 后端依赖（Python 包）")
    # (模块名, 中文说明, 是否必需)
    required = [
        ("fastapi", "Web 框架", True),
        ("uvicorn", "ASGI 服务器", True),
        ("pydantic", "配置模型", True),
        ("yaml", "YAML 配置解析（PyYAML）", True),
        ("loguru", "日志", True),
        ("aiohttp", "异步 HTTP 客户端", True),
        ("numpy", "数值计算", True),
        ("openai", "OpenAI 兼容 LLM SDK", True),
        ("multipart", "控制面板图片上传（python-multipart）", True),
        ("ddgs", "搜索工具后端（DuckDuckGo）", True),
        ("yt_dlp", "视频下载工具后端（yt-dlp）", True),
    ]
    optional = [
        ("chromadb", "长期记忆向量库（memory-vector）"),
        ("faster_whisper", "本地语音识别（asr-whisper）"),
    ]

    all_ok = True
    missing_required = []
    for mod, desc, _ in required:
        if importlib.util.find_spec(mod) is not None:
            ok(f"{desc}（{mod}）已安装。")
        else:
            fail(f"{desc}（{mod}）未安装。")
            missing_required.append(mod)
            all_ok = False

    for mod, desc in optional:
        if importlib.util.find_spec(mod) is not None:
            ok(f"[可选] {desc} 已安装。")
        else:
            warn(f"[可选] {desc} 未安装——对应功能会降级或关闭，需要时再装。")

    if missing_required:
        print(f"\n       缺少必需依赖，请在项目根目录执行：{C.BOLD}pip install -e .{C.RESET}")
    return all_ok


def check_frontend() -> bool:
    """检查前端依赖是否安装（frontend/node_modules）。属可选：不装则桌宠起不来，但后端能跑。"""
    title("4. 前端依赖（Electron 桌宠）")
    node_modules = PROJECT_ROOT / "frontend" / "node_modules"
    pkg = PROJECT_ROOT / "frontend" / "package.json"
    if not pkg.exists():
        warn("找不到 frontend/package.json——确认前端目录完整。")
        return True
    if node_modules.exists() and any(node_modules.iterdir()):
        ok("前端依赖已安装（frontend/node_modules 存在）。")
    else:
        warn("前端依赖未安装——桌面窗口起不来（后端仍可单独运行）。")
        print(f"       安装：{C.BOLD}cd frontend && npm install{C.RESET}"
              f"（或首次 Start.bat 会自动装）。")
    return True


def check_data_dirs() -> bool:
    """检查 data/ 目录结构是否就绪（首次运行时后端也会自动建，这里只是提示）。"""
    title("5. 数据目录 data/")
    data_dir = PROJECT_ROOT / "data"
    if data_dir.exists():
        subdirs = [p.name for p in data_dir.iterdir() if p.is_dir()]
        ok(f"data/ 已存在（含 {len(subdirs)} 个子目录，如 memory / affinity / chat_history 等）。")
    else:
        warn("data/ 目录还不存在——首次启动后端时会自动创建，无需手动处理。")
    return True


# =============================================================================
# 汇总与"下一步"建议
# =============================================================================
def main() -> int:
    """跑完所有检查，打印汇总与下一步建议。返回进程退出码（0=可以启动，1=有硬性问题）。"""
    print(f"{C.BOLD}{C.CYAN}")
    print("============================================================")
    print("  White Salary - 首次运行自检")
    print("============================================================")
    print(f"{C.RESET}项目根目录：{PROJECT_ROOT}")

    # 逐项检查；hard_ok 只由"硬性"检查（Python / conf / 依赖）决定
    py_ok = check_python()
    conf_ok = check_conf()
    dep_ok = check_dependencies()
    check_frontend()      # 前端与数据目录属提示性，不计入硬性通过条件
    check_data_dirs()

    hard_ok = py_ok and conf_ok and dep_ok

    title("下一步建议")
    if hard_ok:
        ok("核心环境就绪！可以启动了：")
        print(f"       {C.BOLD}Start.bat{C.RESET}                     一键启动（后端 + 桌宠）")
        print(f"       或分步：设置 PYTHONPATH=src 后运行 python run_server.py")
        print(f"       启动后按 {C.BOLD}Ctrl+,{C.RESET} 打开控制面板。祝你和白玩得开心 :)")
        print(f"\n       想要语音/QQ/绘图等可选增强，见 {C.BOLD}docs/INSTALL.md{C.RESET}。")
        exit_code = 0
    else:
        fail("还有必须先解决的问题（见上面标 [X] 的项）。按顺序处理：")
        if not py_ok:
            print("       1) 升级 Python 到 3.11+。")
        if not dep_ok:
            print("       2) 安装后端依赖：pip install -e .")
        if not conf_ok:
            print("       3) 复制并填好 conf.yaml（至少一把主 LLM 密钥），见 docs/CONFIG.md。")
        print(f"\n       改完再跑一次本脚本确认：{C.BOLD}python scripts/first_run_check.py{C.RESET}")
        exit_code = 1

    print()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
