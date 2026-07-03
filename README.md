<div align="center">

# White Salary（白）

**原创的多平台 AI 陪伴体 / 桌宠 · An Original Multi-Platform AI Companion**

一个会聊天、有记忆、有情绪、有好感度、能主动找你说话的 AI 伙伴——
她叫「**白**」，同时以四种形态陪着你：桌面宠物、QQ 机器人、QQ 空间、B 站直播。

<!-- 徽章行（2026-07-03 新手体验（批10）：静态徽章，不接 CI） -->
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python 3.10-3.12](https://img.shields.io/badge/Python-3.10--3.12-blue.svg)
![Tests](https://img.shields.io/badge/tests-695%20passed-brightgreen.svg)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg)

Python 3.10-3.12 · FastAPI · Electron · Live2D · 六边形架构

当前版本：**v0.1.5**

<!-- 2026-07-03：交流群。二维码图放到 docs/assets/qq-group.png 后可取消下一行注释显示图片 -->
<p>
  <strong>💬 交流 / 求助 / 反馈 Bug / 一起开发测试</strong><br>
  QQ 群「白的研发」：<code>2163039710</code><br>
  安装卡住、配置 API、想交流玩法或参与测试，都可以进群一起聊。
</p>

<!-- ![扫码进群](docs/assets/qq-group.png) -->

</div>

---

## ✨ 三步上手（完全新手照这个来）

> 📖 **完全不懂编程？** 看 **[《快速上手 · 图文说明书》](docs/快速上手.md)**——每一步都写清"你会看到什么 → 你该做什么 → 出问题看哪里"，照着走就行。

**不需要懂编程，不需要知道 pip 是什么。** 只做三件事：

> 这是 **Windows 桌面宠物** 的路线。如果你是在 Linux 云服务器 / VPS 上只跑后端，请直接看下面的 [Linux / 服务器一键向导](#linux--服务器一键向导只跑后端)。

| 步骤 | 做什么 | 说明 |
|------|--------|------|
| **① 下载** | 点本页绿色 **Code** 按钮 → **Download ZIP**，解压到任意文件夹（会用 Git 的也可以 `git clone`） | 路径里最好别带奇怪符号 |
| **② 双击 `安装.bat`** | 在解压出来的文件夹里双击它，等它自动装完 | 它会检查 Python 版本、创建项目专用 `.venv`、安装后端与前端依赖、复制配置模板 |
| **③ 粘贴一把 key** | 安装向导会提示你粘贴 API key。去 **[硅基流动注册](https://cloud.siliconflow.cn)**（手机号即可，新号常送免费额度），复制一把 `sk-` 开头的 key 粘进去 | 一把 key 就能打通聊天/看图/语音/生图/生视频云端兜底（详见[全供应商注册表](docs/EXTERNAL_SERVICES.md#providers)） |

装完之后，白就会出现在你的桌面上。以后每次想找她，双击 **`Start.bat`** 即可。

> - 想用别家服务（DeepSeek / Kimi / OpenRouter…）？→ [全供应商注册与拿 key 直达表](docs/EXTERNAL_SERVICES.md#providers)
> - 安装卡住了 / 想手动装？→ [docs/INSTALL.md 详细安装指南](docs/INSTALL.md)
> - 不确定配好没？双击安装后运行 `.venv\Scripts\python.exe scripts\first_run_check.py`，它会用中文告诉你下一步做什么。

---

## 🖼️ 效果预览

<!--
  项目主人注意（2026-07-03 新手体验（批10））：
  在 docs/assets/ 下放两张真实截图（建议命名 desktop-pet.png = 桌宠形态、control-panel.png = 控制面板），
  然后把下面两行取消注释，README 门面效果会好很多。在放出真实截图前请保持注释，不放假图。
-->
<!-- ![桌面宠物形态](docs/assets/desktop-pet.png) -->
<!-- ![控制面板](docs/assets/control-panel.png) -->

> 📷 截图整备中。先用文字脑补一下：透明置顶、鼠标穿透的 Live2D 桌宠悬浮在屏幕一角，配一套科幻冰晶风的对话 UI 与 22 页控制面板。

---

## 这是什么

**White Salary** 是一个原创的 AI 陪伴体项目。核心是一个叫「白」的角色——21 岁、银白长发、清冷温柔、说话极简的虚拟角色，有完整的人格设定、长期记忆、情绪和对你的好感度。她不是一个问答机器人，而是一个**会记住你、会想你、会主动找你聊天**的伙伴，对标 Neuro-sama 的自然陪伴感，融合 Manus 式的自主工具执行能力。

白**同时以四种形态存在**（可按需只开一种，也可全开）：

| 形态 | 说明 | 依赖 |
|------|------|------|
| 🖥️ **Electron 桌面宠物** | 透明全屏置顶、鼠标穿透、Live2D 形象 + 口型同步、科幻冰晶 UI、语音对话 | 必装（主形态） |
| 💬 **QQ 机器人** | 经 NapCat（OneBot v11）私聊 + 群聊，多用户好感度 | 需装 NapCat（可选） |
| 🌐 **QQ 空间社交** | 自动发说说 / 评论 / 逛空间 / 兴趣匹配 / 限流 | 需 QQ 登录（可选） |
| 📺 **B 站直播** | 弹幕监听 + 回复 | 需在项目 `.venv` 中安装 `.[bilibili]`（可选） |

<!-- 2026-07-03 便捷化文档：破除"语音必须本地"误解，强调一把云端 key 全功能 -->
> ⭐ **一把云端 key 点亮全部 AI 能力**：只要一把[硅基流动](https://cloud.siliconflow.cn) key，**聊天 / 语音识别 / 语音合成 / 看图 / 生图 / 生视频云端兜底全部开箱即用，无需安装任何本地模型**。默认配置全走云端，不吃你的显卡。下面这些"本地版"都是**可选进阶**，不装照样全功能：

| 能力 | 默认（云端，一把 key 就能用） | 本地版（可选进阶） |
|------|------------------------------|-------------------|
| 聊天 | ✅ 云端大模型 | Ollama 等本地 LLM |
| 语音识别 ASR | ✅ 云端 SenseVoice | faster-whisper |
| 语音合成 TTS | ✅ 云端 CosyVoice2 兜底 | GPT-SoVITS |
| 看图 | ✅ 云端 Qwen3-VL | 本地多模态 |
| 生图 / 生视频 | ✅ 云端 FLUX / Wan2.2 | ComfyUI |

> **诚实说明**：桌面聊天只要一把 LLM 密钥就能跑起来。QQ / QQ 空间 / B 站等平台形态是**可选增强**，不接也能用核心功能，只是对应形态关闭。详见 [docs/INSTALL.md](docs/INSTALL.md)。

---

## 特性亮点

- 🧠 **五层记忆系统**：短期（20 轮）/ 核心（永久）/ 重要（90 天）/ 长期（向量检索，可选 ChromaDB）/ 知识图谱（13 类实体）。全项目最大子系统。
- 💕 **好感度系统**：11 级关系（厌恶→知己）+ 独立"家人"关系、等级效率系数、每日衰减、里程碑奖励、300+ 关键词检测、多用户分账。
- 😊 **情感系统**：心情分 0–100、6 种情绪、情绪惯性，联动 Live2D 表情与 TTS 语速音调。
- ⚡ **并行工具流式对话**：主模型流式输出的同时，工具判断模型并行决策——不需要工具时零延迟。
- 🗣️ **语音对话**：逐句流式 TTS、按住说话、持续监听、语音打断、多段合并（本地 GPT-SoVITS 优先，云端兜底）。
- 🤖 **主动行为**：闲置主动聊天（早安 / 关心 / 追问）、休息模式、每晚写第一人称日记。
<!-- 2026-07-03 便捷化文档：强调控制面板图形化配置，不用碰配置文件 -->
- 🎛️ **控制面板（全程图形化，不用碰配置文件）**：22 页设置面板，桌宠上按 `Ctrl+,` 打开，QQ / 语音 / LLM / B站 / QQ空间 / 人设等每页填表单→点保存→点『重启后端』按钮即可生效，**完全不需要手动编辑 conf.yaml、不需要命令行**。涵盖 LLM 多供应商 / 多角色、记忆管理、好感度编辑、知识图谱 CRUD、插件市场等。
- 🔌 **插件系统**：Python 插件自动发现 + 沙箱 + 热重载 + GitHub 市场。
- 🎨 **多媒体生成（可选）**：本地 ComfyUI 文生图 / 图生视频，三级降级到云端。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10-3.12 · FastAPI · Uvicorn · WebSocket · Pydantic · PyYAML · loguru |
| 前端 | Electron 28 · PixiJS · pixi-live2d-display · Three.js · D3 |
| LLM | OpenAI 兼容适配器（支持 SiliconFlow / DeepSeek / Claude / NVIDIA / Ollama 等 13+ 提供商），主模型 + 7 个分角色模型 |
| 语音 | GPT-SoVITS（本地 TTS）/ SiliconFlow CosyVoice2（云端兜底）/ faster-whisper（本地 ASR）/ Silero VAD |
| 记忆 | SQLite + JSON + ChromaDB（可选向量）+ bge-m3 重排 |
| 图像 | ComfyUI（本地）/ DMXAPI / SiliconFlow FLUX（云端） |

---

## 架构（六边形 / Hexagonal Architecture）

核心业务逻辑不依赖任何具体技术，所有 AI 组件都能通过配置切换。

```
                       ┌─────────────────────────────────────┐
                       │              入口 / 装配              │
                       │   run_server.py（手工装配依赖）       │
                       └──────────────────┬──────────────────┘
                                          │
        ┌─────────────────────────────────┼─────────────────────────────────┐
        │                                 │                                 │
┌───────▼────────┐              ┌─────────▼─────────┐             ┌─────────▼────────┐
│     core/      │              │    adapters/      │             │ infrastructure/  │
│   纯业务逻辑    │◄────接口────►│   技术实现        │             │  服务器 / 配置    │
│                │              │                   │             │                  │
│ · agent 对话   │              │ · llm  (13+提供商) │             │ · server (FastAPI│
│ · memory 记忆  │              │ · asr / tts / vad │             │   + WebSocket)   │
│ · affinity好感 │              │ · vision 视觉      │             │ · config 加载器   │
│ · emotion 情感 │              │ · platform        │             │                  │
│ · personality  │              │   (QQ/QZone/B站)  │             │                  │
│ · filter 过滤  │              │ · tools / 插件     │             │                  │
│ · qzone/social │              │ · comfyui 绘图     │             │                  │
└────────────────┘              └───────────────────┘             └──────────────────┘
```

- **core**：对话、记忆、情感、好感度、人格、过滤——纯 Python，不碰任何 SDK。
- **adapters**：LLM / ASR / TTS / VAD / Vision / 平台 / 工具的具体实现，全部可插拔。
- **infrastructure**：FastAPI 服务器、WebSocket、配置加载。

> 更多结构细节见 [CONTRIBUTING.md](CONTRIBUTING.md) 。

---

## Windows 手动安装 / Linux 服务器安装

> Windows 桌宠新手请直接用最上面的 [✨ 三步上手](#-三步上手完全新手照这个来)（双击 `安装.bat`）。
> Linux / VPS / 云服务器用户请直接跳到下面的 [Linux / 服务器一键向导](#linux--服务器一键向导只跑后端)。
> 更完整说明见 **[docs/INSTALL.md](docs/INSTALL.md)**；配置项详解见 **[docs/CONFIG.md](docs/CONFIG.md)**。

### Windows 手动安装（5 步跑起来桌面聊天）

**环境要求**：Windows 10/11 · Python 3.10-3.12（建议 3.11+）· Node.js 18+

```bash
# 1. 装后端依赖（在项目根目录；全部装进项目专用 .venv，不污染全局 Python）
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e .

# 2. 装前端依赖
cd frontend && npm install && cd ..

# 3. 复制配置模板
copy conf.default.yaml conf.yaml        # PowerShell/CMD
# cp conf.default.yaml conf.yaml        # Git Bash

# 4. 填一把主 LLM 密钥（最小可用配置：只填这一把就能桌面文字聊天）
#    最省心：先 Start.bat 启动，在桌宠上按 Ctrl+, 打开控制面板 → LLM配置页
#            → 填 api_key / model 等 → 点保存 → 点『重启后端』按钮，全程图形化。
#    （进阶用户也可直接编辑 conf.yaml 的 llm 节：api_key / provider / model / base_url）

# 5. 一键启动（会拉起后端 + 前端桌宠）
Start.bat
```

### Linux / 服务器一键向导（只跑后端）

服务器 / VPS / Linux 面板用户不要跑 Windows 的 `安装.bat`，直接用服务器向导：

```bash
git clone https://github.com/VBHC-UHY/white-salary.git
cd white-salary
chmod +x server-setup.sh
./server-setup.sh
```

它会自动调用 `install.sh` 创建 `.venv`、安装后端依赖、生成 `conf.yaml`，然后提示你粘贴一把 SiliconFlow key、选择端口、选择是否安装 ChromaDB 长期记忆、是否创建 systemd 服务。新手一路回车也能先跑起来。

只想一条命令快速配置：

```bash
WS_API_KEY=sk-你的key ./server-setup.sh --yes
```

需要长期向量记忆：

```bash
./server-setup.sh --with-memory
```

装完后前台启动：

```bash
source .venv/bin/activate
PYTHONPATH=src python run_server.py --host 0.0.0.0 --port 12400
```

检查是否启动成功：

```bash
curl http://127.0.0.1:12400/health
```

> `server-setup.sh` 是给新手的服务器向导；`install.sh` 是底层安装器，适合懂 Linux 的用户手动控制。两者都会创建项目专用 `.venv`，不会污染系统 Python。Linux 脚本会优先使用 Python 3.12 / 3.11 / 3.10，暂不选择 Python 3.13，避免部分可选 AI 依赖还没跟上新版解释器。

**不确定配好了没？** 跑一次首次运行自检：

```bash
.venv\Scripts\python.exe scripts\first_run_check.py
```

它会检查 conf.yaml、主 LLM 密钥、Python 版本、关键依赖、目录结构，并用中文告诉你「下一步做什么」。

---

## 四种形态各自的启用方式

### 🖥️ 桌面宠物（默认，最小可用）
装好依赖 + 填一把 LLM 密钥 + `Start.bat` 即可。这是主形态，其它三种都在它之上叠加。

<!-- 2026-07-03 便捷化文档：NapCat 是独立程序不放进项目文件夹；配置改走控制面板 QQ 页 -->
### 💬 QQ 机器人
1. 自行下载并登录 [NapCat](https://github.com/NapNeko/NapCatQQ)（第三方**独立开源程序**，**不随本仓库分发、也不用放进 White Salary 文件夹**——下载后放哪都行、双击自己运行；白通过网络端口与它通信。新手强烈推荐它的一键版 NapCat.OneKey）。
2. 在 NapCat 里配置**正向 WebSocket**（记下端口，配一个 token）。
3. **打开控制面板**（在桌宠上按 `Ctrl+,`）→ 进 **QQ 配置**页 → 把 NapCat 给你的端口（`ws_url`）、`token` 填进去，开启 QQ、填上你自己的 QQ 号（会被认成"主人"）→ 点保存 → 点『重启后端』按钮。（进阶用户也可直接改 `conf.yaml` 的 `qq` 节。）
4. 详见 [docs/CONFIG.md](docs/CONFIG.md) 的 `qq` 节。

### 🌐 QQ 空间社交
在控制面板（`Ctrl+,`）的 QQ 空间页扫码 / Cookie 登录后即可自动发说说、逛空间。

### 📺 B 站直播
安装可选依赖 `.venv\Scripts\python.exe -m pip install -e ".[bilibili]"`，在控制面板 B 站页登录后开启弹幕监听。框架完整，属可选增强。

---

## 可选增强组件（不装会怎样）

| 组件 | 作用 | 不装的后果 |
|------|------|-----------|
| **NapCat** | QQ 私聊 / 群聊接入 | QQ 形态关闭，桌面聊天不受影响 |
| **GPT-SoVITS**（本地 TTS） | 白的本地语音合成 | 自动降级到云端 CosyVoice2；两者都没配则无语音输出，文字照常 |
| **ComfyUI** | 本地文生图 / 图生视频 | 绘图降级到云端 API；都没配则绘图工具不可用 |
| **ChromaDB** | 长期记忆向量检索 | 长期记忆引擎关闭（`memory.long_term_provider: none`），其余记忆层照常 |
| **faster-whisper** | 本地语音识别（ASR） | 降级到云端 ASR；语音输入需要至少一种 |

安装与配置细节全部在 [docs/INSTALL.md](docs/INSTALL.md) 的"可选组件"分节。想**纯云端**跑通这些能力（不装任何本地组件）见 [docs/EXTERNAL_SERVICES.md](docs/EXTERNAL_SERVICES.md)；想把某几项换成**本地大模型**见 [docs/LOCAL_ADVANCED.md](docs/LOCAL_ADVANCED.md)。

---

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+,` | 打开控制面板 |
| `F12` | 开发者工具 |
| `Ctrl+Q` | 退出应用 |
| `Ctrl+Shift+R` | 重载前端 |

---

## 文档导航

| 文档 | 讲什么 | 适合谁 |
|------|--------|--------|
| 📦 [docs/INSTALL.md](docs/INSTALL.md) | 详细安装指南（含可选组件） | 想手动装 / 安装出问题时排查 |
| ☁️ [docs/EXTERNAL_SERVICES.md](docs/EXTERNAL_SERVICES.md) | 云端服务配置 + **全供应商注册直达表** | **新手主推**，填 key 就能用 |
| 💻 [docs/LOCAL_ADVANCED.md](docs/LOCAL_ADVANCED.md) | 本地大模型进阶指南 | 想免费 / 离线 / 定制 |
| ⚙️ [docs/CONFIG.md](docs/CONFIG.md) | 配置项逐节详解（含即时生效 / 需重启标注） | 想精细调参 |
| 🤝 [CONTRIBUTING.md](CONTRIBUTING.md) | 参与开发（架构导览、跑测试、代码规范） | 开发者 |
| 📝 [CHANGELOG.md](CHANGELOG.md) | 版本更新日志 | 想了解版本变化 |
| 📖 [docs/快速上手.md](docs/快速上手.md) | 完全新手图文安装说明书 | 完全不懂编程 |

---

## 致谢

- **参考与致敬**：自然陪伴感对标 [Neuro-sama](https://en.wikipedia.org/wiki/Neuro-sama)，自主工具执行理念参考 Manus；前端视觉参考 my-neuro 后独立实现。白的性格是她自己的，不抄任何人。
- **开源依赖**：FastAPI · Uvicorn · Pydantic · loguru · Electron · PixiJS · pixi-live2d-display · ChromaDB · GPT-SoVITS · faster-whisper · Silero VAD · NapCat 等众多优秀开源项目。

---

## 许可证

本项目源代码采用 **MIT** 许可证，详见 [LICENSE](LICENSE)。

> ⚠️ **以下内容不在 MIT 许可范围内**，请以各自来源 / 说明为准：
> - `prompts/` 下"白"的人设文本（原创人设）；
> - `live2d_models/` 下随仓库提供的公开可分发 Live2D 示例 / 参考模型资源；
> - `assets/tts/` 下的参考音频；
> - `NapCat/`、`NapCat_OneKey/` 等第三方组件。
>
> 分发前请自行确认上述资源是否允许再分发。若你希望更换许可证（如 AGPL），见 [LICENSE](LICENSE) 顶部说明。
