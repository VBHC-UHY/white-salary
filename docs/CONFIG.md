# White Salary 配置详解

本文逐节讲清 `conf.yaml` 每一项的作用，以及**哪些改完即时生效、哪些要重启后端**。

<!-- 2026-07-03 便捷化文档：先讲图形面板法，手改 conf.yaml 是进阶备选 -->
> 🎛️ **不想碰配置文件？完全没问题。** 本文列的每一节，绝大多数都能在**图形控制面板**里填：启动后在桌宠上按 `Ctrl+,` 打开控制面板 → 找到对应页（QQ / 语音 / LLM / B站 / QQ空间 / 人设 等）→ 填好表单 → 点保存 → 点『重启后端』按钮，面板会自动写回 `conf.yaml`，**不用命令行、不用手动编辑文件**。本文是给想**逐项理解 / 手动精调**的进阶用户看的参考；下文出现的"改 `conf.yaml`""重启后端"都能用面板一键完成。

- **配置文件**：`conf.default.yaml`（默认模板，别改）+ `conf.yaml`（你的覆盖，深合并——只写想改的项）。
- **安全**：`conf.yaml` 已被 `.gitignore` 忽略，密钥不会进 Git。密钥**只**放 `conf.yaml`，永远不要写进 `conf.default.yaml` 或任何会提交的文件。
- **生效时机总原则**：`conf.yaml` 在后端**启动时被读入并缓存**，所以**绝大多数配置项改完需要重启后端**才生效。少数走独立文件 / 运行实例的项支持热更新，见文末的[生效时机总表](#生效时机总表)。

---

## LLM 多角色架构（核心）

白用 **1 个主模型 + 7 个分角色模型**，每个角色可用不同提供商 / 模型，避免互相抢配额、也能给每类任务挑最合适的模型。每一节都是同样的四个字段：`provider` / `api_key` / `model` / `base_url`（主 `llm` 额外有 `temperature` / `max_tokens`）。

| 节 | 角色 | 干什么 | 留空的后果 |
|----|------|--------|-----------|
| `llm` | **主对话** | 日常聊天，最核心 | **必填**，否则白不会说话 |
| `llm_tool` | 工具判断 | Function Calling 决策（要不要调工具） | 工具能力受限 / 关闭 |
| `llm_memory` | 记忆分析 | 从对话里提取值得记住的信息 | 长期记忆提取、用户画像学习不工作 |
| `llm_emotion` | 情感分析 | 理解情绪、调语气 | 走关键词兜底，LLM 情感分析关闭 |
| `llm_vision` | 视觉理解 | 看图 / 看屏幕（多模态） | 若主 `llm` 是硅基流动会自动复用 key；否则图片理解、截屏识别不可用 |
| `llm_postprocess` | 后处理 | 配文、快速辅助杂活 | 相应辅助功能降级 |
| `llm_detect` | 检测防护 | 安全检测、话题追踪、风格记忆 | 相应检测降级 |
| `llm_background` | 后台任务 | 不紧急的后台任务、自动聊天 | 后台任务降级 |

**最小可用**：只填 `llm` 一节就能桌面文字聊天。想让记忆、情感、看图全工作，再逐个填分角色。

> ⚠️ **模型会被上游下架**：本项目历史上就因为 `llm_memory` / `llm_vision` 配的模型被提供商下架，导致记忆提取 / 看图静默瘫痪。启动时后端有 LLM 通道自检（1-token 探活 + ERROR 横幅告警），若看到某通道告警，去对应节换一个在架模型即可。控制面板 LLM 页也有"测试连通"按钮。

示例（任选一家 OpenAI 兼容提供商）：

```yaml
llm:
  provider: "siliconflow"
  api_key: "你的密钥"
  model: "deepseek-ai/DeepSeek-V3.2"
  base_url: "https://api.siliconflow.cn/v1"
  temperature: 0.7        # 采样温度，越高越发散
  max_tokens: 2048        # 单次回复最大 token
```

---

## `system` / `server` 系统与服务器

```yaml
system:
  name: "White Salary"    # 项目名（别改）
  version: "0.1.11"
  debug: false            # true=更多日志
  log_level: "INFO"       # DEBUG / INFO / WARNING / ERROR

server:
  host: "localhost"       # localhost=只本机；0.0.0.0=局域网可访问
  port: 12400             # 后端端口（前端也连这个）
  cors_enabled: true
  cors_origins:           # 允许跨域的来源
    - "http://localhost:3000"
    - "http://localhost:5173"
```

> 改 `port` 后前端连接地址也要对应（前端默认连 `ws://localhost:12400/ws/chat`）。

---

## `asr` 语音识别

```yaml
asr:
  provider: "siliconflow"                 # 当前仅支持 siliconflow（云端）
  api_key: ""                             # 留空=自动从角色 LLM 里扫一把 SiliconFlow 密钥
  model: "FunAudioLLM/SenseVoiceSmall"
```

- `api_key` 留空时，后端会自动从你已配置的角色 LLM 中找一把 SiliconFlow 密钥复用——所以若某个 LLM 节已经用了 SiliconFlow，这里可以不用重复填。
- 想用本地 ASR（faster-whisper）见 [INSTALL.md](INSTALL.md) 6.5。

---

## `tts` 语音合成

```yaml
tts:
  local_api_url: "http://127.0.0.1:9880"          # 本地 GPT-SoVITS 地址（主 TTS）
  ref_audio: "assets/tts/ref_default.wav"         # 声音克隆参考音频（相对项目根，也支持绝对路径）
  ref_text: "..."                                  # 参考音频对应的文字
  fallback_provider: "siliconflow"                 # 本地不可用时的云端兜底
  fallback_api_key: ""                             # 留空=自动扫 SiliconFlow 密钥
  fallback_model: "FunAudioLLM/CosyVoice2-0.5B"
  fallback_voice: "..."                            # 兜底音色 ID
```

- **优先级**：启动时探测 `local_api_url` 端口——通了走本地 GPT-SoVITS，不通则用云端 `fallback_*`。两者都没有则只有文字。
- `ref_audio` / `ref_text` 也可用环境变量 `WS_TTS_REF_AUDIO` / `WS_TTS_REF_TEXT` 覆盖（优先级更高，便于换机器不改配置）。
- 情绪会联动语速（心情 × 语速倍率），这是运行时行为，无需配置。

---

## `vad` 语音活动检测

```yaml
vad:
  provider: "silero"      # silero
  threshold: 0.5          # 0~1，越高越严格；太高会漏掉小声说话
```

---

## `memory` 记忆系统

```yaml
memory:
  short_term_max_turns: 20        # 短期记忆保留最近多少轮
  long_term_provider: "none"      # 长期记忆向量库：chroma / none
  long_term_top_k: 5              # 长期检索返回条数
```

- `long_term_provider: none`（默认）时长期记忆引擎关闭，其余四层记忆（短期 / 核心 / 重要 / 知识图谱）照常。想开语义检索，装 ChromaDB 后设为 `chroma`（见 [INSTALL.md](INSTALL.md) 6.4）。
- 更细的记忆行为（遗忘曲线 / 关联 / 场景 / 模块启禁等）在独立文件 `config/memory_settings.json`，其中 `modules.disabled` 列表默认禁用了约 20 个拟人化候选模块（文件保留，改配置即恢复）。**这些改动都需重启**。

---

## `emotion` 情感系统

```yaml
emotion:
  enabled: true           # 关掉则情感系统不工作
  sensitivity: 0.6        # 0~1，越高情绪变化越频繁
```

---

## `personality` 人格

```yaml
personality:
  system_prompt_file: "prompts/system_prompt.txt"   # 人设文件（白的性格全在这）
  character_name: "White Salary"
```

- 白的人设是 `prompts/system_prompt.txt`（600+ 行，原创，不在 MIT 许可范围内）。
- 通过控制面板改人设时，后端会**尝试热更新**运行中的人格（成功即时生效，失败则提示重启）。直接改这里的路径配置本身仍需重启。

---

## `qq` QQ 集成（可选形态）

```yaml
qq:
  enabled: false                  # true 才接入 QQ
  ws_url: "ws://127.0.0.1:3001"   # NapCat 正向 WebSocket 地址
  bot_name: "白"                  # 群里 @ 或提到这个名字才回复
  token: ""                       # 与 NapCat WebSocket 配置里的 token 一致
  family_qq: []                   # 家人 QQ 号列表
  owner_name: ""                  # 对主人的称呼（可选）
```

- **`family_qq` 很关键**：列表里的 QQ 号会被设为"家人"关系（不受好感度衰减），且**第一个号同时作为主人的统一 user_id**——桌面端与 QQ 端因此共用一套身份、好感度、画像、对话日志。想让"桌面的白"和"QQ 的白"认识同一个你，就把你的 QQ 号放这里第一位。
- `owner_name`：白对主人的称呼。留空时按用户画像的 `user_name` 回退，再没有就让模型自然称呼（**不会硬编码叫"主人"**）。
<!-- 2026-07-03 便捷化文档：QQ 配置推荐用控制面板 QQ 页 -->
- **最省心的填法**：打开控制面板（在桌宠上按 `Ctrl+,`）→ 进 **QQ 配置**页填上面这些项 → 点保存 → 点『重启后端』按钮，无需手动编辑本节。完整接入步骤（含 NapCat 下载运行）见 [INSTALL.md](INSTALL.md) 6.2。
- NapCat / OneBot 重新连接后会自动补拉遗漏历史；补回消息与实时消息走同一套 QQ 处理流程，并用持久消息 ID / 游标去重。该能力不需要单独开关。

---

## `external_tools` 可选本地工具路径

```yaml
external_tools:
  napcat_launcher: ""  # NapCat目录或launcher*.bat；空=只查项目下NapCat/
  comfyui_bat: ""      # ComfyUI启动脚本
  comfyui_input: ""    # ComfyUI input目录
  gpt_sovits_dir: ""   # GPT-SoVITS安装目录
  cosyvoice_bat: ""    # CosyVoice启动脚本
  wav2lip_dir: ""      # Wav2Lip安装目录
  ffmpeg_path: ""      # ffmpeg可执行文件；空=查系统PATH
```

- 推荐在控制面板 **终端控制室 → 本地工具路径**里填写，保存后启动按钮会立即使用新路径。
- 环境变量优先级高于配置，对应名称见 [LOCAL_ADVANCED.md](LOCAL_ADVANCED.md#6-关于-external_tools-配置节路径怎么配)。
- 这些都是可选本地增强。只使用云端 API 时全部留空，不会回退到作者电脑的固定路径。
- Linux 服务器不会执行 Windows `.bat`；需要 QQ 时应单独运行 OneBot 桥接，并配置 `qq.ws_url`。

---

## `filter` 内容过滤

```yaml
filter:
  enabled: true                           # 防系统信息泄露的安全过滤
  rules_file: "prompts/filter_rules.yaml"
```

---

## `avatar` 虚拟形象

```yaml
avatar:
  provider: "none"                        # live2d / none
  model_path: "live2d_models/default"     # Live2D 模型目录
```

> 桌面端 Live2D 由前端加载渲染；后端这里主要作记录。`live2d_models/` 里的模型资源按各自来源 / 说明使用，不并入源码 MIT 许可。

---

## `auto_chat` 主动聊天

```yaml
auto_chat:
  enabled: true
  morning_greeting: true      # 早安问候
  night_greeting: true        # 晚安问候
  care_reminder: true         # 关心提醒
  random_chat: true           # 随机找你聊
  daily_limit: 3              # 每日主动次数上限
```

主动行为还受好感度系数约束（关系越好越常主动）。

---

## `features` 功能开关

```yaml
features:
  topic_tracker: true         # 话题追踪
  rest_system: true           # 休息模式（睡觉/生气/疲劳）
  user_learning: true         # 用户画像学习
  memory_consolidation: true  # 记忆整理（每日凌晨）
  content_filter: true        # 内容过滤（运行时开关）
```

> 这 5 个开关在批 6 中已"接活"——关掉是真的关。改动后点控制面板的『重启后端』按钮即可生效（无需命令行）。

---

## `singing` 唱歌（可选）

```yaml
singing:
  enabled: false
  provider: "rvc"
  model_path: "models/singing/default.pth"
```

> 当前 RVC 依赖链与主环境 `numpy` 版本冲突，不建议把 `rvc-python` 装进 White Salary 的 `.venv`。需要唱歌/变声时，请把 RVC 放到独立环境或外部服务中。

---

## 常见配置场景

### 场景 A：只要桌面聊天（最省事）
只填 `llm` 一节。`qq.enabled: false`、`memory.long_term_provider: none`、不配 TTS。→ 文字聊天、记忆（除长期向量外）、好感度、情感、桌宠全部可用。

### 场景 B：桌面 + 语音 + 长期记忆
在 A 基础上：启动本地 GPT-SoVITS（或配 `tts.fallback_*` 云端）；在项目 `.venv` 里执行 `python -m pip install -e ".[memory-vector]"` 后设 `memory.long_term_provider: chroma`；填 `llm_memory` / `llm_emotion` 让记忆情感更聪明。

### 场景 C：全开（桌面 + QQ + QQ空间 + 绘图）
在 B 基础上：装 NapCat，配 `qq.*` 并 `qq.enabled: true`、`family_qq` 填自己 QQ；控制面板登录 QQ 空间 / B 站；装 ComfyUI 做本地绘图；把 8 个 LLM 角色都填齐。

---

## 生效时机总表

| 改动内容 | 生效方式 |
|----------|----------|
| `conf.yaml` 里**任意节**（LLM / server / memory / qq / features / tts …） | **需重启后端**（启动时缓存） |
| `config/memory_settings.json`（含模块启禁） | **需重启后端** |
| 通过控制面板改人设 / 提示词 | 尝试**热更新**，成功即时生效，失败提示重启 |
| `config/expression_map.json`（表情映射） | **即时**（运行时实时读取） |
| 控制面板"清空对话 / QQ 上下文" | 有运行实例注入时**即时**；否则清文件、运行中记忆需重启 |
| 控制面板"用户好感度 / 黑名单"等运行实例操作 | 多为**即时**（作用于运行中实例） |

<!-- 2026-07-03 便捷化文档：重启后端点面板按钮即可，不用命令行 -->
> 拿不准时，改完配置就重启一次后端最稳妥。**重启不用命令行**——控制面板右下角就有『重启后端』按钮，点一下即可（用面板改配置的话，保存后顺手点它就生效了）。

---

配置改完不确定对不对？跑一次自检：Windows 用 `.venv\Scripts\python.exe scripts\first_run_check.py`，Linux/macOS 用 `.venv/bin/python scripts/first_run_check.py`。
安装相关见 [INSTALL.md](INSTALL.md)；参与开发见 [../CONTRIBUTING.md](../CONTRIBUTING.md)。
