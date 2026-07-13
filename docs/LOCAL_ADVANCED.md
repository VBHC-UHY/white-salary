# 本地大模型进阶指南

> **先说结论**：白**默认走云端就够用**（见 [EXTERNAL_SERVICES.md](EXTERNAL_SERVICES.md)），本文是给**想免费 / 离线 / 可定制**的进阶用户的。
> 每一项本地组件都遵循同一个原则——**不装它，白会自动降级到云端**，主聊天永远不受影响。所以你可以只挑自己在意的那一两项装（比如只装 GPT-SoVITS 训练白的专属声音），其余继续用云端。

本文诚实标注每项的**大概体积**和**不装的后果**，方便你决定值不值得。

---

## 目录

- [0. 先看这张对照表](#0-先看这张对照表)
- [1. ComfyUI —— 本地生图 / 生视频](#1-comfyui--本地生图--生视频)
- [2. GPT-SoVITS —— 本地语音克隆（训练白的专属声音）](#2-gpt-sovits--本地语音克隆训练白的专属声音)
- [3. ffmpeg —— 语音 / 视频格式处理（很多本地功能的前置）](#3-ffmpeg--语音--视频格式处理很多本地功能的前置)
- [4. faster-whisper —— 本地语音识别](#4-faster-whisper--本地语音识别)
- [5. Wav2Lip / 本地 CosyVoice（可选，简述）](#5-wav2lip--本地-cosyvoice可选简述)
- [6. 关于 `external_tools` 配置节（路径怎么配）](#6-关于-external_tools-配置节路径怎么配)

---

## 0. 先看这张对照表

| 本地组件 | 作用 | 大概体积 | 不装的后果（自动降级到） |
|----------|------|----------|--------------------------|
| **ComfyUI** + 模型 | 本地文生图 / 图生视频 | 程序几 GB + 模型十几~几十 GB | 云端生图（硅基流动 `Qwen/Qwen-Image` / DMXAPI `dall-e-3`）、云端生视频（硅基流动 Wan2.2） |
| **GPT-SoVITS** + 模型 | 本地语音合成 / 声音克隆 | 几 GB | 云端 TTS（硅基流动 CosyVoice2） |
| **ffmpeg** | 语音 / 视频格式转换、拼接、配音合成 | 约 100 MB | 相关本地后处理不可用（长视频拼接、加配音、提取帧等） |
| **faster-whisper** | 本地语音识别（ASR） | 模型几百 MB ~ 几 GB | 云端 ASR（硅基流动 SenseVoice） |
| **Wav2Lip / 本地 CosyVoice** | 口型对齐 / 无过滤本地 TTS | 各几 GB | 对应功能关闭或走云端 |

> **共同原则**：本地组件是"探测到才用"。启动 / 调用时白会探测本地服务是否在线、可执行文件 / 模型是否存在；**探测不到就静默降级到云端**，不会报错阻断。

---

## 1. ComfyUI —— 本地生图 / 生视频

**作用**：让白在你自己的显卡上画图、做图生视频，免费、无云端过滤、画风可自定义。

**降级关系（生图三级）**：本地 ComfyUI → DMXAPI → 硅基流动。**不装 ComfyUI**时，只要配了云端 key，白直接用云端画（见 [EXTERNAL_SERVICES.md 第 6 节](EXTERNAL_SERVICES.md#6-生图--生视频--语音的云端配置)）。

### 1.1 去哪下载、装在哪

- 下载 **ComfyUI**（推荐 Windows 便携整合包 `ComfyUI_windows_portable`）：<https://github.com/comfyanonymous/ComfyUI>
- 解压到任意目录（例如 `<你的ComfyUI目录>`）。整合包自带启动脚本 `run_nvidia_gpu.bat`（N 卡）。
- 需要一张显存够用的 **NVIDIA 显卡**（生图 8GB 起，视频建议 16GB+）。

### 1.2 模型去哪下、放哪

ComfyUI 本身不含大模型，需要另外下载放进它的 `models/` 子目录：

| 用途 | 模型示例 | 放置目录 |
|------|----------|----------|
| 文生图 checkpoint | 动漫风 IllustriousXL / NoobAI-XL 等 `.safetensors` | `ComfyUI/models/checkpoints/` |
| 图生视频 SVD | `svd_xt` | `ComfyUI/models/checkpoints/`（按 workflow 要求） |
| 图生视频 Wan2.2 | Wan2.2 I2V 主模型 + UMT5 文本编码器 + CLIP-Vision-H + Wan2.1 VAE | 按各自节点要求放 `models/` 下对应子目录 |
| 动画 AnimateDiff | SD1.5 底模 + `v3_sd15_mm.ckpt` motion module | `checkpoints/` 与 `animatediff_models/` |

> 模型体积普遍很大：单个 checkpoint 2~7 GB，Wan2.2 全套十几 GB。**整套配齐几十 GB 很正常**，先按你要的功能挑着下。
> 项目自带的 workflow 模板在 `config/comfyui_workflows/`（白遥控 ComfyUI 用的就是它们，**只当遥控器，不改 ComfyUI 的任何文件**）。

### 1.3 怎么让白找到你的 ComfyUI

白通过两个位置定位 ComfyUI：

1. **HTTP API 地址**：固定 `http://127.0.0.1:8188`（ComfyUI 默认端口）。白调用时会先探测这个端口是否在线。
2. **启动脚本路径**：白在需要时可以**自动拉起** ComfyUI，靠的是启动脚本 `run_nvidia_gpu.bat` 的路径。这个路径不再写死在源码里，需要在 `conf.yaml` 或环境变量里填你的实际位置。

改法（**推荐用环境变量，避免改源码**）：

```bat
:: 指向你自己的 ComfyUI 启动脚本
set WS_COMFYUI_BAT=<你的ComfyUI目录>\run_nvidia_gpu.bat
:: 图生视频 / 改图时，白会把输入图片复制进 ComfyUI 的 input 目录
set WS_COMFYUI_INPUT=<你的ComfyUI目录>\ComfyUI\input
```

> 这两个环境变量（`WS_COMFYUI_BAT` / `WS_COMFYUI_INPUT`）是当前版本**已生效**的官方覆盖方式。你也可以先手动把 ComfyUI 跑起来（双击 `run_nvidia_gpu.bat`，监听 8188），白探测到在线就会直接用，连启动脚本路径都不用配。
> 若你的版本 `conf.yaml` 里已经有 `external_tools` 配置节（见[第 6 节](#6-关于-external_tools-配置节路径怎么配)），也可以把路径填在那里。

**不装的后果**：生图自动降级到云端 DMXAPI / 硅基流动；生视频走云端硅基流动 Wan2.2。都没配云端 key 才会"绘图工具不可用"，其它功能正常。

---

## 2. GPT-SoVITS —— 本地语音克隆（训练白的专属声音）

**作用**：本地语音合成，还能**克隆出白的专属音色**。本地 TTS 比云端更可控、无云端过滤、免费。

**降级关系**：本地 GPT-SoVITS（探测端口 `9880`）→ 云端硅基流动 CosyVoice2。**不装**则白用云端说话，或纯文字。

### 2.1 下载安装

- 下载 **GPT-SoVITS**：<https://github.com/RVC-Boss/GPT-SoVITS>（推荐整合包）。
- 解压安装到任意目录（例如 `<你的GPT-SoVITS目录>`，按你自己的实际路径填写）。
- 启动它的推理 API：运行 `api_v2.py`，让它监听 `127.0.0.1:9880`。
- 白在启动时会**探测 9880 端口**：在线就走本地，不在线就走云端。

### 2.2 参考音频放哪

声音克隆需要一段**参考音频**（几秒到十几秒的清晰人声）。项目约定放在 `assets/tts/`：

- 默认参考音频：`assets/tts/ref_default.wav`
- 对应的参考文字与音频路径在 `conf.yaml` 的 `tts` 节：

```yaml
tts:
  local_api_url: "http://127.0.0.1:9880"                # 本地 GPT-SoVITS 地址
  ref_audio: "assets/tts/ref_default.wav"               # 参考音频（相对项目根 / 绝对路径皆可）
  ref_text: "你怎么不会想让我去试辣子鸡丁吧"              # 参考音频里说的那句话
```

> 环境变量 `WS_TTS_REF_AUDIO` / `WS_TTS_REF_TEXT` 优先级更高，可临时覆盖。

### 2.3 如何训练白的专属声音（7 步一键流程）

项目提供了**一键训练脚本**，把 GPT-SoVITS 的完整训练链路自动跑一遍：

- 入口：`scripts/train_voice.bat`（内部调 `scripts/train_voice.py`）。
- 流程共 **7 步**（脚本会自动依次执行，支持断点续跑）：
  1. **切分** —— 把长音频切成小段；
  2. **降噪** —— 清理每段音频；
  3. **ASR 转写** —— 自动识别每段说了什么（FunASR 优先，退回 Faster-Whisper）；
  4. **特征提取** —— 提取 BERT / HuBERT / 语义特征；
  5. **训练 SoVITS 模型**；
  6. **训练 GPT 模型**；
  7. **更新配置** —— 找到最新权重并写回 `tts_infer.yaml`，训练完即可直接用。

> 使用前请把你的 GPT-SoVITS 安装路径填到 `conf.yaml` 的 `external_tools.gpt_sovits_dir`，或设置环境变量 `WS_GPT_SOVITS_DIR`。`scripts/train_voice.bat` 会读取这个配置，并使用 GPT-SoVITS 自带的 `venv_new` 虚拟环境。准备好一段白的目标音色素材，跑一次即可得到专属声音。

**不装的后果**：白用云端硅基流动 CosyVoice2 说话（在 `tts.fallback_*` 配硅基流动 key）；连云端也没配则只有文字，聊天不受影响。

---

## 3. ffmpeg —— 语音 / 视频格式处理（很多本地功能的前置）

**作用**：ffmpeg 是音视频处理的瑞士军刀，白在这些场景需要它：

- 长视频**分段拼接**；
- 给视频**加配音**（音视频合流）；
- 提取视频**最后一帧**做图生视频接力；
- 语音格式转换。

**体积**：约 100 MB，很小，**建议装上**——它是不少本地音视频功能的隐形前置。

### 3.1 下载与配置

- 下载 **ffmpeg**（Windows 构建）：<https://www.gyan.dev/ffmpeg/builds/> 或 <https://ffmpeg.org/download.html>，解压得到 `bin/ffmpeg.exe`。
- 让白找到它，**二选一**：

  **方式 A（推荐）——加进系统 PATH**：把 `ffmpeg.exe` 所在的 `bin` 目录加入系统环境变量 `PATH`，白会自动 `which ffmpeg` 找到它。这是最省事、最通用的方式。

  **方式 B ——在配置里显式填写路径**：在 `conf.yaml` 的 `external_tools.ffmpeg_path` 填 `ffmpeg.exe` 的完整路径，例如 `<你的ffmpeg目录>\bin\ffmpeg.exe`。

> 若你的版本 `conf.yaml` 里有 `external_tools.ffmpeg_path`（见[第 6 节](#6-关于-external_tools-配置节路径怎么配)），也可以直接把 `ffmpeg.exe` 的绝对路径填在那里。

**不装的后果**：依赖 ffmpeg 的本地后处理（长视频拼接、加配音、提取帧）不可用，会在日志里提示"找不到 ffmpeg"；不影响单段生成、聊天、云端功能。

---

## 4. faster-whisper —— 本地语音识别

**作用**：在本地把你的语音转成文字（ASR），免费、离线。

**降级关系**：本地 faster-whisper → 云端硅基流动 SenseVoice。

### 4.1 安装

```bash
pip install -e ".[asr-whisper]"
```

首次使用会自动下载 whisper 模型权重（几百 MB ~ 几 GB，取决于模型规格）。

**不装的后果**：语音识别走云端硅基流动 SenseVoice（`asr` 节配硅基流动 key）；本地云端**至少要有一种**才能语音输入，纯文字聊天不受影响。

---

## 5. Wav2Lip / 本地 CosyVoice（可选，简述）

这两项属于更小众的进阶玩法，简述如下：

- **本地 CosyVoice2**：给视频加配音时，若云端配音不可用，白会尝试调用**本地 CosyVoice**（`cosyvoice_client`）作为备选。它是**无云端过滤**的本地 TTS，需要你**单独部署** CosyVoice 服务后才生效。不部署则该备选自动跳过，走云端配音。体积几 GB。
- **Wav2Lip**（口型对齐）：用于让画面口型对上配音，属实验性增强。**不装无影响**——桌面端的口型同步默认走 Live2D 的音频驱动，不依赖 Wav2Lip。若你要做"照片 / 视频人物对口型"这类玩法，再单独部署 Wav2Lip。体积几 GB。

> 这两项目前都是"可选增强"，绝大多数用户**不需要装**。默认路径（云端配音 + Live2D 口型）已经够用。

---

## 6. 关于 `external_tools` 配置节（路径怎么配）

所有可选本地组件都集中在 `external_tools`。最简单的做法是打开控制面板 **终端控制室 → 本地工具路径**，填写后保存；它会写入 `conf.yaml`，启动按钮马上使用新位置。

需要脚本化部署时，也可以直接写配置：

```yaml
external_tools:
  napcat_launcher: "<NapCat目录或launcher.bat>"
  comfyui_bat: "<ComfyUI目录>/run_nvidia_gpu.bat"
  comfyui_input: "<ComfyUI目录>/ComfyUI/input"
  gpt_sovits_dir: "<GPT-SoVITS目录>"
  cosyvoice_bat: "<CosyVoice启动脚本>"
  wav2lip_dir: "<Wav2Lip目录>"
  ffmpeg_path: "<ffmpeg目录>/bin/ffmpeg.exe"
```

解析顺序始终是：**环境变量 → `conf.yaml` → 安全自动发现**。环境变量适合临时覆盖：

| 配置 | 环境变量 |
|------|----------|
| `napcat_launcher` | `WS_NAPCAT_LAUNCHER` |
| `comfyui_bat` / `comfyui_input` | `WS_COMFYUI_BAT` / `WS_COMFYUI_INPUT` |
| `gpt_sovits_dir` | `WS_GPT_SOVITS_DIR` |
| `cosyvoice_bat` | `WS_COSYVOICE_BAT` |
| `wav2lip_dir` | `WS_WAV2LIP_DIR` |
| `ffmpeg_path` | `WS_FFMPEG_PATH` |

NapCat 留空时只会查找项目目录下的 `NapCat/launcher*.bat`；ffmpeg 留空时查系统 `PATH`；其它本地模型留空时不会猜作者电脑路径。**不装本地组件时可以全部留空，白会走云端降级或明确提示对应增强不可用。**

---

**相关文档**：
- 云端配置（主推、小白友好）→ [EXTERNAL_SERVICES.md](EXTERNAL_SERVICES.md)
- 配置项逐条详解 → [CONFIG.md](CONFIG.md)
- 从零安装（含可选组件）→ [INSTALL.md](INSTALL.md)
