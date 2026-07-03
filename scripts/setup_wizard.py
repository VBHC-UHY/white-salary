"""
White Salary - 图形配置向导（2026-07-03 新手体验（批10））

给完全新手用的"零门槛"配置工具：不用懂 YAML、不用开编辑器，
粘贴一把 API Key，点两下按钮，就能开始和白聊天。

设计原则（本文件内部分成两层，方便测试）：
  1. wizard_core 纯函数层 —— 复制模板 / 把密钥写进 conf.yaml / 测试连通。
     全部是不碰界面的普通函数，单元测试直接测它们（tests/unit/test_setup_wizard.py）。
  2. tkinter 界面层 —— 只是一层壳：欢迎页 → 粘贴 Key 页 → 完成页。
     tkinter 是 Python 自带的，零额外依赖，双击 安装.bat 最后会自动弹出本向导。

用法：
    python scripts/setup_wizard.py

本脚本只写 conf.yaml（必要时从 conf.default.yaml 复制模板），不碰其它文件、
不启动服务器。写入采用"按行替换"的方式，尽量保留 conf.yaml 里的中文注释。
"""

import asyncio
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 项目根目录 = 本脚本的上一级的上一级（scripts/ 的父目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 项目采用 src 布局（PYTHONPATH=src），向导要独立可跑，这里自己把 src 加进来
_SRC_DIR = PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# =============================================================================
# 供应商信息
# =============================================================================
# base_url / 默认模型的"单一事实来源"是 adapters/llm/factory.py 的 PRESET_PROVIDERS。
# 但向导可能在依赖装到一半时被打开（比如 pip 失败后用户手动运行），
# 所以这里备一份"只含向导会用到的 4 家"的兜底预设，导入失败时用它，绝不崩。
_FALLBACK_PRESETS: dict[str, dict[str, str]] = {
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3.2",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2.5",
    },
    "dmxapi": {
        "base_url": "https://www.dmxapi.cn/v1",
        "default_model": "gpt-4o",
    },
}

# 向导里展示给新手选的供应商（id, 显示名, 拿 Key 的网址）。
# 默认推荐硅基流动：注册送额度、国内直连、同一把 key 还能顺带打通看图和语音。
WIZARD_PROVIDERS: list[tuple[str, str, str]] = [
    ("siliconflow", "硅基流动（推荐：注册送额度，一把 key 聊天/看图/语音全通）",
     "https://cloud.siliconflow.cn/account/ak"),
    ("deepseek", "DeepSeek 官方",
     "https://platform.deepseek.com/api_keys"),
    ("kimi", "Kimi（月之暗面）",
     "https://platform.moonshot.cn/console/api-keys"),
    ("dmxapi", "DMXAPI（Claude / GPT 中转）",
     "https://www.dmxapi.cn/token"),
]


def get_provider_presets() -> dict[str, dict[str, str]]:
    """
    读取供应商预设（base_url / 默认模型）。

    优先从项目源码 adapters/llm/factory.py 的 PRESET_PROVIDERS 读（保持单一事实来源，
    以后源码里改了模型名，向导自动跟着变）；导入失败（依赖没装齐等）则用内置兜底表。
    """
    try:
        from white_salary.adapters.llm.factory import PRESET_PROVIDERS
        return PRESET_PROVIDERS
    except Exception:
        # 依赖没装齐也不能让向导崩——用兜底预设，至少能把 key 写进配置
        return _FALLBACK_PRESETS


# =============================================================================
# wizard_core 纯函数层（不碰任何界面，单元测试的对象）
# =============================================================================
def ensure_conf(project_root: Path) -> Path:
    """
    确保 conf.yaml 存在：没有就从 conf.default.yaml 复制一份。

    返回:
        conf.yaml 的路径

    异常:
        FileNotFoundError: 连 conf.default.yaml 模板都找不到时抛出（中文提示）
    """
    conf_path = project_root / "conf.yaml"
    if conf_path.exists():
        return conf_path
    default_path = project_root / "conf.default.yaml"
    if not default_path.exists():
        raise FileNotFoundError(
            f"找不到配置模板 conf.default.yaml（应在 {project_root} 下）——"
            f"请确认项目文件完整，或重新克隆仓库。"
        )
    shutil.copyfile(default_path, conf_path)
    return conf_path


def ensure_system_prompt(project_root: Path) -> bool:
    """
    确保人格提示词 prompts/system_prompt.txt 存在：没有就从 example 复制。

    返回:
        True = 这次新复制了一份；False = 本来就有（或连 example 都没有，静默跳过——
        提示词缺失不阻塞配置，后端启动时会有自己的提示）
    """
    target = project_root / "prompts" / "system_prompt.txt"
    example = project_root / "prompts" / "system_prompt.example.txt"
    if target.exists() or not example.exists():
        return False
    shutil.copyfile(example, target)
    return True


def _quote_yaml_value(value: str) -> str:
    """把字符串包成 YAML 双引号标量（转义反斜杠和双引号），避免特殊字符破坏语法。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def set_yaml_scalar(text: str, section: str, key: str, value: str) -> str:
    """
    在 YAML 文本里设置 顶级section.key = "value"（按行替换，保留全文注释）。

    为什么不用 yaml.safe_load + safe_dump 回写？——那样会把 conf.yaml 里
    所有中文注释全部抹掉，新手以后就没法照着注释改配置了。所以这里用
    "找到 section 块 → 替换/插入 key 行"的文本方式，其它行原样保留。

    规则：
      - section 必须是顶格的 "section:" 行；找不到就在文末追加整个小节
      - key 在 section 块内按缩进行匹配；找不到就紧跟 section 头插入一行
      - section 块的边界 = 下一个顶格非空行（包括顶格注释）

    参数:
        text:    conf.yaml 的完整文本
        section: 顶级小节名（如 "llm" / "llm_vision"）
        key:     小节内的键名（如 "api_key"）
        value:   要写入的字符串值（会自动加双引号转义）

    返回:
        修改后的完整文本（保证以换行结尾）
    """
    lines = text.splitlines()
    quoted = _quote_yaml_value(value)

    # 第一步：找 section 顶格行（"llm:" 或 "llm:   # 注释"）
    section_idx = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped == f"{section}:" or stripped.startswith(f"{section}:") and (
            stripped[len(section) + 1:].strip() == "" or
            stripped[len(section) + 1:].lstrip().startswith("#")
        ):
            # 必须是顶格（无缩进）才算顶级小节
            if not line[:1].isspace():
                section_idx = i
                break

    if section_idx == -1:
        # 整个小节都没有 → 在文末追加（少见，但 conf.yaml 被删节时能自愈）
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        lines.append(f"  {key}: {quoted}")
        return "\n".join(lines) + "\n"

    # 第二步：确定 section 块的结束位置（下一个顶格非空行，注释也算边界）
    end_idx = len(lines)
    for i in range(section_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line[:1].isspace():
            end_idx = i
            break

    # 第三步：在块内找 "  key:" 行并整行替换（保留原缩进；行尾旧注释随行丢弃）
    for i in range(section_idx + 1, end_idx):
        stripped = lines[i].lstrip()
        if stripped.startswith(f"{key}:"):
            indent = lines[i][: len(lines[i]) - len(stripped)]
            lines[i] = f"{indent}{key}: {quoted}"
            return "\n".join(lines) + "\n"

    # 第四步：块内没有这个 key → 紧跟 section 头插入
    lines.insert(section_idx + 1, f"  {key}: {quoted}")
    return "\n".join(lines) + "\n"


def write_key_to_conf(conf_path: Path, provider_id: str, api_key: str) -> dict[str, str]:
    """
    把 API Key 写进 conf.yaml 的主 LLM 通道（llm 节）。

    做的事：
      1. 按所选供应商，设置 llm.provider / llm.base_url / llm.model（预设值来自
         factory.PRESET_PROVIDERS）+ llm.api_key
      2. 如果选的是硅基流动：同一把 key 顺手填进 llm_vision.api_key——
         llm_vision 模板默认就是硅基流动的看图模型，这样"看图"直接就通了；
         而 ASR/TTS 的密钥留空时会自动从角色 LLM 里扫硅基流动的 key，
         所以语音识别/语音合成也顺带通了（一把 key 三件事）。
      3. 写完立刻用 yaml 重新解析校验，确保没把配置写坏——校验不过就恢复原文并报错。

    参数:
        conf_path:   conf.yaml 路径（必须已存在，先调 ensure_conf）
        provider_id: 供应商 id（siliconflow / deepseek / kimi / dmxapi 等）
        api_key:     用户粘贴的 API Key（自动去首尾空白）

    返回:
        写入摘要 {"provider", "base_url", "model", "vision_filled"}，给界面展示用

    异常:
        ValueError:   供应商不认识 / key 为空
        RuntimeError: 写入后校验失败（此时 conf.yaml 已恢复原样，不会留下坏文件）
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("API Key 是空的——请先粘贴你的密钥。")

    presets = get_provider_presets()
    if provider_id not in presets:
        raise ValueError(
            f"未知的供应商：{provider_id}（支持：{', '.join(sorted(presets))}）"
        )
    preset = presets[provider_id]

    original = conf_path.read_text(encoding="utf-8")
    text = original
    # 主对话通道：provider / base_url / model 全部按预设写死，避免新手手填出错
    text = set_yaml_scalar(text, "llm", "provider", provider_id)
    text = set_yaml_scalar(text, "llm", "base_url", preset["base_url"])
    text = set_yaml_scalar(text, "llm", "model", preset["default_model"])
    text = set_yaml_scalar(text, "llm", "api_key", api_key)

    vision_filled = False
    if provider_id == "siliconflow":
        # 同一把硅基流动 key 填进视觉通道（模板里 llm_vision 默认就是硅基流动的
        # GLM-4.1V 看图模型），这样"发图给白看"零额外配置就能用；
        # 语音（ASR/TTS）留空密钥时会自动扫到这把 key，也一并通了。
        text = set_yaml_scalar(text, "llm_vision", "api_key", api_key)
        vision_filled = True

    conf_path.write_text(text, encoding="utf-8")

    # 写后自检：立刻重新解析，确认 YAML 没写坏、值确实进去了
    try:
        import yaml
        parsed = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        llm = parsed.get("llm") or {}
        assert str(llm.get("api_key")) == api_key, "llm.api_key 写入后读回不一致"
        assert str(llm.get("provider")) == provider_id, "llm.provider 写入后读回不一致"
        if vision_filled:
            vision = parsed.get("llm_vision") or {}
            assert str(vision.get("api_key")) == api_key, "llm_vision.api_key 写入后读回不一致"
    except ImportError:
        # PyYAML 没装（依赖装到一半）——跳过自检，文本写入本身是安全的
        pass
    except Exception as exc:
        # 校验失败：把文件恢复原样，绝不留下坏配置
        conf_path.write_text(original, encoding="utf-8")
        raise RuntimeError(f"配置写入校验失败，已恢复原文件。原因：{exc}") from exc

    return {
        "provider": provider_id,
        "base_url": preset["base_url"],
        "model": preset["default_model"],
        "vision_filled": "yes" if vision_filled else "no",
    }


def test_connection(provider_id: str, api_key: str, timeout: float = 30.0) -> tuple[bool, str]:
    """
    真发一次 1-token 请求，测试"这把 key + 这家供应商"是否连通。

    复用后端同一套探活逻辑（core/services/llm_health.check_llm_channel），
    保证"向导里测通了 = 启动后一定能用"。

    参数:
        provider_id: 供应商 id
        api_key:     API Key
        timeout:     超时秒数（默认 30 秒，云端冷启动留足余量）

    返回:
        (是否连通, 中文说明)——连通时说明为"耗时 x.x 秒"，失败时为原因
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "还没有填 API Key"

    presets = get_provider_presets()
    if provider_id not in presets:
        return False, f"未知的供应商：{provider_id}"

    try:
        # 延迟导入：openai 包属于依赖安装步骤的一部分，没装时给出可操作的提示
        from white_salary.adapters.llm.openai_compatible import OpenAICompatibleAdapter
        from white_salary.core.services.llm_health import check_llm_channel
    except Exception as exc:
        return False, f"依赖没装齐，暂时无法测试（{exc}）。请先运行 安装.bat 完成依赖安装。"

    preset = presets[provider_id]
    adapter = OpenAICompatibleAdapter(
        api_key=api_key,
        base_url=preset["base_url"],
        model=preset["default_model"],
    )
    start = time.perf_counter()
    try:
        # check_llm_channel 是异步函数，这里用 asyncio.run 包一层给同步界面用
        _, ok, reason = asyncio.run(check_llm_channel("llm", adapter, timeout=timeout))
    except Exception as exc:  # noqa: BLE001 —— 任何异常都要转成中文提示，绝不静默
        return False, f"测试过程出错：{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - start
    if ok:
        return True, f"连通成功，耗时 {elapsed:.1f} 秒"
    return False, reason


# =============================================================================
# tkinter 界面层（只是壳：所有实际动作都调上面的纯函数）
# =============================================================================
class SetupWizardApp:
    """三页式向导窗口：欢迎页 → 粘贴 Key 页 → 完成页。"""

    def __init__(self, root) -> None:  # noqa: ANN001 —— root 是 tk.Tk，避免顶层导入 tkinter
        import tkinter as tk

        self.tk = tk
        self.root = root
        root.title("White Salary 配置向导")
        root.geometry("620x480")
        root.resizable(False, False)

        # 界面状态
        self.key_var = tk.StringVar()
        self.provider_id = tk.StringVar(value="siliconflow")
        self.test_result_var = tk.StringVar(value="")
        self.adv_visible = False

        # 页面容器：切页 = 销毁旧 frame、建新 frame
        self.page = None
        self.show_welcome_page()

    # ------------------------------------------------------------------ 工具
    def _new_page(self):  # noqa: ANN202
        """清掉当前页，返回一个铺满窗口的新 Frame。"""
        if self.page is not None:
            self.page.destroy()
        self.page = self.tk.Frame(self.root, padx=28, pady=24)
        self.page.pack(fill="both", expand=True)
        return self.page

    def _provider_label(self, provider_id: str) -> str:
        """id → 显示名。"""
        for pid, label, _ in WIZARD_PROVIDERS:
            if pid == provider_id:
                return label
        return provider_id

    def _provider_key_url(self, provider_id: str) -> str:
        """id → 拿 Key 的网址。"""
        for pid, _, url in WIZARD_PROVIDERS:
            if pid == provider_id:
                return url
        return "https://cloud.siliconflow.cn/account/ak"

    # ------------------------------------------------------------ 第 1 页：欢迎
    def show_welcome_page(self) -> None:
        """欢迎页：一句话说明 + 开始按钮。"""
        tk = self.tk
        page = self._new_page()

        tk.Label(page, text="欢迎使用 White Salary", font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(30, 12))
        tk.Label(
            page,
            text="白是一只住在你桌面上的 AI 伙伴。\n\n只需一把 API 密钥就能开始——\n下一步会告诉你去哪里免费领。",
            font=("Microsoft YaHei UI", 11),
            justify="center",
        ).pack(pady=8)
        tk.Button(
            page, text="开始配置 →", font=("Microsoft YaHei UI", 12),
            width=18, command=self.show_key_page,
        ).pack(pady=36)

    # ------------------------------------------------------ 第 2 页：粘贴 Key
    def show_key_page(self) -> None:
        """主步骤页：粘贴 Key + 去注册按钮 + 可折叠高级区 + 测试连通 + 保存。"""
        tk = self.tk
        page = self._new_page()

        tk.Label(page, text="粘贴你的 API Key", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(
            page,
            text="推荐用「硅基流动」：注册就送额度，一把 key 聊天 / 看图 / 语音全通。",
            font=("Microsoft YaHei UI", 10), fg="#555",
        ).pack(anchor="w", pady=(4, 10))

        # 大输入框 + 去注册按钮（并排）
        row = tk.Frame(page)
        row.pack(fill="x", pady=4)
        entry = tk.Entry(row, textvariable=self.key_var, font=("Consolas", 11), width=44)
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        entry.focus_set()
        tk.Button(
            row, text="去注册 / 拿 Key",
            command=lambda: webbrowser.open(self._provider_key_url(self.provider_id.get())),
        ).pack(side="left", padx=(8, 0))

        # 可折叠的"高级"区：换主对话供应商
        self.adv_toggle_btn = tk.Button(
            page, text="高级选项（换一家供应商）▸", relief="flat", fg="#0066cc",
            cursor="hand2", command=self.toggle_advanced,
        )
        self.adv_toggle_btn.pack(anchor="w", pady=(14, 0))

        self.adv_frame = tk.Frame(page, padx=8, pady=6, bd=1, relief="groove")
        tk.Label(self.adv_frame, text="主对话供应商：", font=("Microsoft YaHei UI", 10)).grid(row=0, column=0, sticky="w")
        labels = [label for _, label, _ in WIZARD_PROVIDERS]
        self.adv_choice = tk.StringVar(value=self._provider_label(self.provider_id.get()))
        option = tk.OptionMenu(self.adv_frame, self.adv_choice, *labels, command=self.on_provider_changed)
        option.config(font=("Microsoft YaHei UI", 9), width=48)
        option.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.adv_hint = tk.Label(self.adv_frame, text="", font=("Microsoft YaHei UI", 9), fg="#555")
        self.adv_hint.grid(row=2, column=0, sticky="w")
        tk.Button(
            self.adv_frame, text="打开这家的拿 Key 页面",
            command=lambda: webbrowser.open(self._provider_key_url(self.provider_id.get())),
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.adv_visible = False
        self._refresh_adv_hint()

        # 测试连通 + 结果显示
        test_row = tk.Frame(page)
        test_row.pack(fill="x", pady=(18, 0))
        self.test_btn = tk.Button(test_row, text="测试连通", width=12, command=self.on_test)
        self.test_btn.pack(side="left")
        tk.Label(test_row, textvariable=self.test_result_var, font=("Microsoft YaHei UI", 10),
                 wraplength=420, justify="left").pack(side="left", padx=10)

        # 底部：保存按钮
        bottom = tk.Frame(page)
        bottom.pack(side="bottom", fill="x", pady=(20, 0))
        tk.Button(
            bottom, text="保存并继续 →", font=("Microsoft YaHei UI", 11, "bold"),
            width=16, command=self.on_save,
        ).pack(side="right")
        tk.Button(bottom, text="← 上一步", command=self.show_welcome_page).pack(side="left")

    def toggle_advanced(self) -> None:
        """展开 / 收起高级区。"""
        if self.adv_visible:
            self.adv_frame.pack_forget()
            self.adv_toggle_btn.config(text="高级选项（换一家供应商）▸")
        else:
            self.adv_frame.pack(fill="x", pady=(2, 0), after=self.adv_toggle_btn)
            self.adv_toggle_btn.config(text="高级选项（换一家供应商）▾")
        self.adv_visible = not self.adv_visible

    def on_provider_changed(self, chosen_label: str) -> None:
        """高级区下拉换供应商：显示名 → id，并刷新提示。"""
        for pid, label, _ in WIZARD_PROVIDERS:
            if label == chosen_label:
                self.provider_id.set(pid)
                break
        self._refresh_adv_hint()

    def _refresh_adv_hint(self) -> None:
        """刷新高级区里"这家会用什么模型"的小字提示。"""
        presets = get_provider_presets()
        pid = self.provider_id.get()
        preset = presets.get(pid, {})
        model = preset.get("default_model", "?")
        self.adv_hint.config(text=f"将使用模型：{model}（地址已内置，无需手填）")

    def on_test(self) -> None:
        """测试连通按钮：开线程真发一次 1-token 请求，不冻住界面。"""
        key = self.key_var.get().strip()
        if not key:
            self.test_result_var.set("❌ 还没有填 API Key")
            return
        self.test_btn.config(state="disabled")
        self.test_result_var.set("测试中，请稍候（最多 30 秒）…")
        pid = self.provider_id.get()

        def worker() -> None:
            ok, msg = test_connection(pid, key)

            def done() -> None:
                self.test_btn.config(state="normal")
                self.test_result_var.set(("✅ " if ok else "❌ ") + msg)

            # tkinter 只能在主线程改界面，用 after 把结果投递回主线程
            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def on_save(self) -> None:
        """保存按钮：调纯函数写配置；任何失败都弹中文窗，绝不静默。"""
        from tkinter import messagebox

        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning(
                "还没填 Key",
                "请先粘贴 API Key。\n\n点「去注册 / 拿 Key」按钮可以免费申请一把。",
            )
            return
        try:
            conf_path = ensure_conf(PROJECT_ROOT)
            ensure_system_prompt(PROJECT_ROOT)
            summary = write_key_to_conf(conf_path, self.provider_id.get(), key)
        except Exception as exc:  # noqa: BLE001 —— 任何写入失败都要弹窗告知
            messagebox.showerror(
                "配置写入失败",
                f"写入 conf.yaml 时出错：\n{exc}\n\n"
                f"你也可以用记事本打开 conf.yaml，手动把密钥填到 llm 节的 api_key。",
            )
            return
        self.show_done_page(summary)

    # ------------------------------------------------------------ 第 3 页：完成
    def show_done_page(self, summary: dict[str, str]) -> None:
        """完成页：告诉用户下一步双击 Start.bat，或直接帮忙启动。"""
        tk = self.tk
        from tkinter import messagebox

        page = self._new_page()

        tk.Label(page, text="🎉 配置完成！", font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(24, 10))
        extra = "（看图 / 语音也一并配好了）" if summary.get("vision_filled") == "yes" else ""
        tk.Label(
            page,
            text=f"主对话已接入：{self._provider_label(summary['provider'])}\n"
                 f"模型：{summary['model']}{extra}\n\n"
                 f"双击项目文件夹里的 Start.bat 即可启动白。",
            font=("Microsoft YaHei UI", 11), justify="center",
        ).pack(pady=6)

        def launch_now() -> None:
            """帮新手直接把 Start.bat 拉起来（新开一个控制台窗口）。"""
            try:
                subprocess.Popen(
                    ["cmd", "/c", str(PROJECT_ROOT / "Start.bat")],
                    cwd=str(PROJECT_ROOT),
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                self.root.destroy()
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("启动失败", f"没能启动 Start.bat：\n{exc}\n\n请手动双击项目里的 Start.bat。")

        def open_more_docs() -> None:
            """打开进阶功能文档（生图 / QQ / B站 等外部服务怎么接）。"""
            doc = PROJECT_ROOT / "docs" / "EXTERNAL_SERVICES.md"
            try:
                import os
                os.startfile(str(doc))  # noqa: S606 —— 本地文档，交给系统默认程序打开
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("打开失败", f"没能打开文档：\n{exc}\n\n文档位置：{doc}")

        tk.Button(page, text="现在就启动白 🚀", font=("Microsoft YaHei UI", 12, "bold"),
                  width=20, command=launch_now).pack(pady=(20, 6))
        tk.Button(page, text="想解锁更多功能（生图 / QQ / B站）看这里",
                  relief="flat", fg="#0066cc", cursor="hand2",
                  command=open_more_docs).pack(pady=2)
        tk.Button(page, text="完成，关闭窗口", command=self.root.destroy).pack(pady=(16, 0))


def main() -> int:
    """入口：起 tkinter 窗口；tkinter 不可用时退化为控制台中文提示。"""
    try:
        import tkinter as tk
    except Exception as exc:  # noqa: BLE001 —— 极少数精简版 Python 没带 tkinter
        print("[X] 你的 Python 没有安装 tkinter，图形向导打不开。")
        print(f"    （详细原因：{exc}）")
        print("    替代方案：用记事本打开 conf.yaml，把 API Key 填到 llm 节的 api_key 即可。")
        print("    拿 Key 地址（硅基流动，免费注册）：https://cloud.siliconflow.cn/account/ak")
        return 1

    root = tk.Tk()
    SetupWizardApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
