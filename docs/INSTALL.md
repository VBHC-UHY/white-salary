# White Salary 安装指南

本指南带你从零把 White Salary 跑起来。分三个层次：

1. **最小可用**：只要桌面文字聊天 —— 装依赖 + 填一把 LLM 密钥即可（约 5 分钟）。
2. **完整桌面体验**：加上本地语音（GPT-SoVITS）、长期记忆（ChromaDB）。
3. **多平台形态**：QQ 机器人、QQ 空间、B 站直播、AI 绘图。

> 配置项的逐节详解见 [CONFIG.md](CONFIG.md)。遇到问题先跑 `python scripts/first_run_check.py` 自检。
>
> **外部 AI 服务怎么配**：白的智能功能都来自外部 AI 服务。**最省事的云端方式**（注册→拿 key→填配置，不用下模型）见 [EXTERNAL_SERVICES.md](EXTERNAL_SERVICES.md)；想免费 / 离线 / 定制的**本地大模型进阶**（ComfyUI / GPT-SoVITS / ffmpeg 等）见 [LOCAL_ADVANCED.md](LOCAL_ADVANCED.md)。

---

## 0. 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| 操作系统 | **Windows 10 / 11** | 启动脚本、端口清理、部分工具为 Windows 专用；其它平台可跑后端但未做适配 |
| Python | **3.10+** | `pyproject.toml` 声明 `>=3.11`，但项目实测在 **3.10 上也能正常运行**（全量测试基线即在 3.10 环境通过）。建议有条件时用 3.11+ |
| Node.js | **18+**（建议 LTS） | 前端 Electron 桌宠 |
| Git | 任意 | 克隆仓库用 |

> 路径若含空格（例如默认的 `D:\White Salary`），在命令行里记得给路径加引号。

---

## 1. 获取代码

```bash
git clone <仓库地址> "White Salary"
cd "White Salary"
```

---

## 2. 安装后端依赖

在**项目根目录**（有 `pyproject.toml` 的地方）执行。推荐先建虚拟环境：

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows CMD:
.venv\Scripts\activate.bat
# Git Bash:
source .venv/Scripts/activate
```

然后以可编辑模式安装项目（会自动装齐所有**必需**依赖）：

```bash
pip install -e .
```

这会安装：FastAPI、Uvicorn、WebSocket、Pydantic、PyYAML、loguru、aiofiles、aiohttp、numpy，以及两个**运行时实际会用到、容易被漏掉**的库：

- **`python-multipart`** —— 控制面板上传图片（multipart 表单）需要它，缺了图片上传会报错。
- **`ddgs`** —— `web_search` 等搜索工具的后端（DuckDuckGo），缺了搜索工具不可用。

> 这两个已经写进 `pyproject.toml` 的主依赖，`pip install -e .` 会自动装。若你是用旧的 `requirements` 方式手动装，务必补上。

### 可选依赖分组（按需装）

`pyproject.toml` 用 `optional-dependencies` 分了组，按需安装：

```bash
pip install -e ".[llm-openai]"      # OpenAI SDK（用 openai 兼容接口时）
pip install -e ".[llm-anthropic]"   # Anthropic Claude SDK
pip install -e ".[asr-whisper]"     # 本地语音识别 faster-whisper
pip install -e ".[memory-vector]"   # 长期记忆向量库 ChromaDB
pip install -e ".[tts-edge]"        # Edge TTS（注：当前版本 Edge TTS 未接入，装了也暂不生效）
pip install -e ".[all]"             # 一次装齐上面全部（体积较大）
pip install -e ".[dev]"             # 开发工具：pytest / ruff / mypy / pre-commit
```

> **注意**：项目的 LLM 适配器是"OpenAI 兼容"实现，通过 HTTP 直连各家 API，**大多数情况下不强制安装 `openai`/`anthropic` SDK** 也能聊天。上面这些 SDK 是为特定路径准备的，先跑起来再按需补。

---

## 3. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

会装 Electron 28 及 axios / cors / express / sql.js 等。首次安装会下载 Electron 二进制，耗时视网络而定。

> `Start.bat` 首次运行时若发现 `frontend/node_modules` 不存在，会自动帮你 `npm install`——所以这一步也可以交给启动脚本。

---

## 4. 配置 API 密钥（最少填一把就能跑）

**复制配置模板**（`conf.default.yaml` 是模板，不要直接改它；你的密钥只放进 `conf.yaml`）：

```bash
copy conf.default.yaml conf.yaml     # Windows CMD / PowerShell
# cp conf.default.yaml conf.yaml     # Git Bash
```

> `conf.yaml` 已被 `.gitignore` 忽略，**绝不会被提交到 Git**——你的密钥安全。深合并机制下，`conf.yaml` 只需写你想改的项，其余走 `conf.default.yaml` 默认值。

### 最小可用配置：只填主 LLM 一节

打开 `conf.yaml`，把 `llm` 节填成你自己的（任选一家 OpenAI 兼容的提供商）：

```yaml
llm:
  provider: "siliconflow"                       # 提供商名（自定义标识即可）
  api_key: "你的密钥"                            # ← 必填！
  model: "deepseek-ai/DeepSeek-V3.2"            # 该提供商支持的模型名
  base_url: "https://api.siliconflow.cn/v1"     # 该提供商的 API 地址
  temperature: 0.7
  max_tokens: 2048
```

**只要这一节填对，就能桌面文字聊天。** 其它 7 个分角色模型（`llm_tool` / `llm_memory` / `llm_emotion` / `llm_vision` / `llm_postprocess` / `llm_detect` / `llm_background`）留空时会有相应功能降级/关闭，但不影响主聊天。想让记忆提取、情感分析、看图等全都工作，再逐个填。各节含义见 [CONFIG.md](CONFIG.md)。

> 支持的提供商：SiliconFlow、DeepSeek、NVIDIA、Moonshot(Kimi)、OpenRouter、DMXAPI、Ollama（本地）等一切 OpenAI 兼容接口。只要给对 `api_key` / `model` / `base_url` 三样即可。

### 验证配置

```bash
python scripts/first_run_check.py
```

看到"主 LLM 密钥已填写"和"关键依赖齐全"就可以启动了。

---

## 5. 启动

### 方式 A：一键启动（推荐）

```bash
Start.bat
```

它会依次：清理旧端口 → （若本机有 GPT-SoVITS）拉起本地 TTS → 启动后端 → 检查/安装前端依赖 → 启动 Electron 桌宠。

> **注意**：`Start.bat` 里的本地 TTS 步骤指向作者机器上的 `D:\AI_Tools\GPT-SoVITS`。如果你没装 GPT-SoVITS，这一步会等待后失败但**不影响后端与桌面**——白会自动用云端 TTS 或纯文字。想跳过等待，可用下面的分步启动只起后端 + 前端。

### 方式 B：分步启动（调试用）

```bash
# 终端 1：后端（必须设 PYTHONPATH=src）
set PYTHONPATH=src        &&  python run_server.py --debug     # CMD
$env:PYTHONPATH="src"     ;   python run_server.py --debug     # PowerShell

# 终端 2：前端
cd frontend && npx electron .
```

或直接用现成脚本：`Start-Backend.bat`（只起后端）、`Start-Frontend.bat`（只起前端）。

启动成功后：

- 后端 HTTP： http://localhost:12400
- 健康检查： http://localhost:12400/health
- WebSocket： ws://localhost:12400/ws/chat
- 桌面窗口会出现白的 Live2D 形象；按 `Ctrl+,` 打开控制面板。

---

## 6. 可选组件（不装也能跑，装了更强）

> **两条路线，任选**：想**纯云端**跑通语音 / 生图 / 生视频（填 key 就行，不装任何本地组件），照 [EXTERNAL_SERVICES.md](EXTERNAL_SERVICES.md) 配即可，可跳过本节大部分本地安装；想把某几项换成**本地大模型**（免费 / 离线 / 定制），本节给了概要，完整安装 / 训练 / 路径配置见 [LOCAL_ADVANCED.md](LOCAL_ADVANCED.md)。

### 6.1 本地语音 GPT-SoVITS（本地 TTS）

- **作用**：让白用本地声音说话（比云端更可控）。
- **不装会怎样**：自动降级到云端 CosyVoice2（需在 `tts.fallback_*` 配 SiliconFlow 密钥）；两者都没有则只有文字，不影响聊天。
- **装法**：单独安装 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，启动它的 `api_v2.py` 监听 `127.0.0.1:9880`。项目会在启动时探测该端口决定走本地还是云端。参考音频路径见 `tts.ref_audio`（默认 `assets/tts/ref_default.wav`）。
- `Start.bat` 里 TTS 那一步的路径（`D:\AI_Tools\GPT-SoVITS`）是作者机器的，**改成你自己的路径**或删掉那段。
- **训练白的专属声音**（一键 7 步流程）、参考音频放置、路径配置等详见 [LOCAL_ADVANCED.md](LOCAL_ADVANCED.md#2-gpt-sovits--本地语音克隆训练白的专属声音)。

### 6.2 QQ 机器人（NapCat）

- **作用**：让白接入 QQ，能私聊 + 群聊，每个人各自积累好感度。
- **不装会怎样**：QQ 形态关闭，桌面聊天完全不受影响。
- **NapCat 是什么**：一个**第三方开源**的 QQ 协议框架（不是本项目做的、也**不随本仓库下载**），负责"登录 QQ 并转发消息"，白通过它收发 QQ 消息。
- **装法**：
  1. **下载 NapCat**：去官方仓库 <https://github.com/NapNeko/NapCatQQ> 的 Releases 下载。**新手强烈建议用一键版 `NapCat.OneKey`**（解压双击就能跑）。
  2. **登录 QQ**：运行 NapCat，扫码或账密登录给白用的 QQ 号（建议用小号，别用主号）。
  3. **开正向 WebSocket**：在 NapCat 的 WebUI（网页配置界面）里新建一个 **"WebSocket 服务器"**（正向 WS），记下**端口**（如 3001）和你设的 **token**。
  4. **配 White Salary**：编辑 `conf.yaml` 的 `qq` 节——`enabled: true`、`ws_url: "ws://127.0.0.1:3001"`（端口对上 NapCat）、`token:`（和 NapCat 里一致）、`family_qq: [你自己的QQ号]`（白会把你认成"主人"）。
  5. **重启后端**。启动日志出现 `[QQ] WebSocket 已连接` 就成了。
- **排查**：白收不到 QQ 消息，99% 是 `ws_url` 端口或 `token` 和 NapCat 里对不上——回第 3、4 步核对。详见 [CONFIG.md](CONFIG.md) 的 `qq` 节。

### 6.3 ComfyUI（本地文生图 / 图生视频）

- **作用**：白能本地画图、做图生视频。
- **不装会怎样**：绘图降级到云端 API（DMXAPI / SiliconFlow FLUX）；都没配则绘图工具不可用，其它功能正常。
- **装法**：安装 [ComfyUI](https://github.com/comfyanonymous/ComfyUI)。项目相关路径可用环境变量覆盖（`WS_COMFYUI_BAT` / `WS_COMFYUI_INPUT`），避免写死。工作流模板在 `config/comfyui_workflows/`。模型下载、显存要求、路径配置的完整说明见 [LOCAL_ADVANCED.md](LOCAL_ADVANCED.md#1-comfyui--本地生图--生视频)。

### 6.4 长期记忆向量库（ChromaDB）

- **作用**：语义检索历史，白能"想起"很久以前聊过的相关内容。
- **不装会怎样**：把 `memory.long_term_provider` 设为 `none`（默认即 none），长期记忆引擎关闭，其余四层记忆照常。
- **装法**：`pip install -e ".[memory-vector]"`，然后 `conf.yaml` 设 `memory.long_term_provider: chroma`。

### 6.5 本地语音识别（faster-whisper）

- **作用**：本地把你的语音转文字（ASR）。
- **不装会怎样**：降级到云端 ASR（SiliconFlow SenseVoice）；语音输入需要至少一种可用。
- **装法**：`pip install -e ".[asr-whisper]"`。

### 6.6 B 站直播

- **作用**：监听直播弹幕并回复。
- **不装会怎样**：B 站形态关闭。
- **装法**：`pip install bilibili-api-python`，在控制面板 B 站页登录后开启。

---

## 7. 跑测试（确认环境健康）

```bash
pip install -e ".[dev]"
set PYTHONPATH=src   &&  python -m pytest tests -q     # CMD
```

基线为 **468 个测试全绿**。若数量对得上、无 FAILED，说明后端环境正常。

---

## 8. 常见问题

| 现象 | 原因 / 解决 |
|------|-------------|
| 启动报 `ModuleNotFoundError: white_salary` | 没设 `PYTHONPATH=src`，或没 `pip install -e .` |
| 后端起了但桌面白屏 / 连不上 | 确认后端在 12400 端口、`http://localhost:12400/health` 返回正常 |
| 图片上传报错 | 缺 `python-multipart`，重装 `pip install -e .` |
| 搜索工具不工作 | 缺 `ddgs`，重装 `pip install -e .` |
| 白不说话（无语音） | 本地 GPT-SoVITS 未启动且未配云端 TTS 兜底；见 6.1 |
| 记忆提取 / 看图不工作 | 对应分角色 LLM（`llm_memory` / `llm_vision`）未填或模型已下架；见 [CONFIG.md](CONFIG.md) |
| QQ 收不到消息 | `qq.enabled`、`ws_url`、`token` 与 NapCat 配置不一致；见 6.2 |

更多配置细节见 **[CONFIG.md](CONFIG.md)**。参与开发见 **[../CONTRIBUTING.md](../CONTRIBUTING.md)**。
