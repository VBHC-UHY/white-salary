# 云端服务配置指南（主推 · 小白友好）

> **一句话**：白的所有"聪明"——聊天、记忆、看图、画图、说话、听你说话——都来自**外部 AI 服务**。
> 最省事的方式是用**云端 API**：去服务商官网注册、拿一把 API key、填进 `conf.yaml`，就能用了，**不用下载任何大模型、不吃你的显卡**。
>
> 想免费 / 离线 / 可定制的进阶用户，再看本地大模型进阶指南：[LOCAL_ADVANCED.md](LOCAL_ADVANCED.md)。

本文面向"只想快点让白跑起来"的你。全程只做三件事：**注册 → 拿 key → 填进配置**。

<!-- 2026-07-03 便捷化文档：显眼处强调一把 key 全功能 + 破除"语音必须本地"误解 + 填 key 走控制面板 -->
> ⭐ **一把 key 点亮全部能力**：只要一把**硅基流动** key，**聊天 / 语音识别 / 语音合成 / 看图 / 生图 全部开箱即用，无需安装任何本地模型**。"语音必须本地"是常见误解——云端 CosyVoice2（说）/ SenseVoice（听）默认就能用。
>
> | 能力 | 默认（云端，一把 key 就能用） | 本地版（可选进阶） |
> |------|------------------------------|-------------------|
> | 聊天 | ✅ 云端大模型 | Ollama 等本地 LLM |
> | 语音识别 ASR | ✅ 云端 SenseVoice | faster-whisper |
> | 语音合成 TTS | ✅ 云端 CosyVoice2 | GPT-SoVITS |
> | 看图 | ✅ 云端 Qwen3-VL | 本地多模态 |
> | 生图 / 生视频 | ✅ 云端 FLUX / Wan2.2 | ComfyUI |
>
> 🎛️ **填 key 最省心的方式**：不用碰配置文件——启动后在桌宠上按 `Ctrl+,` 打开控制面板 → 进 **LLM配置**页填 key → 点保存 → 点『重启后端』按钮即可（详见 [3.2 节](#32-key-填到哪)）。

---

## 目录

- [0. 全供应商注册直达表](#providers)
- [1. 为什么推荐云端](#1-为什么推荐云端)
- [2. 一张表看懂：哪个功能要哪家服务](#2-一张表看懂哪个功能要哪家服务)
- [3. 重点推荐：硅基流动（一家全包）](#3-重点推荐硅基流动一家全包)
  - [3.1 注册与拿 key](#31-注册与拿-key)
  - [3.2 key 填到哪](#32-key-填到哪)
  - [3.3 怎么验证通了（控制面板"测试连通"）](#33-怎么验证通了控制面板测试连通)
- [4. "最小可用"路径：先跑起来，再逐步加](#4-最小可用路径先跑起来再逐步加)
- [5. 分角色模型：想让更多功能工作时逐个填](#5-分角色模型想让更多功能工作时逐个填)
- [6. 生图 / 生视频 / 语音的云端配置](#6-生图--生视频--语音的云端配置)
- [7. 常见问题（FAQ）](#7-常见问题faq)

---

<a id="providers"></a>

## 0. 全供应商注册直达表

<!-- 2026-07-03 新手体验（批10）：所有支持的供应商一览，README「三步上手」的"去注册"链接指到这里 -->

白的 LLM 适配器是 **OpenAI 兼容通用适配器**，下表是项目**内置预设**的（`provider` 填表中标识即可自动带出 `base_url`），也支持任何其它 OpenAI 兼容服务（手填 `base_url`）。

> 各家免费额度 / 页面路径随时会变，标注"以官网为准"的请进官网后自行寻找对应入口。**key 只填进 `conf.yaml`（已被 .gitignore 忽略，不会进 Git），别发给任何人。**

| 供应商 | 官网 / 注册 | 拿 Key 入口 | 填到 conf.yaml 哪里最合适 | 免费情况备注 |
|--------|------------|-------------|--------------------------|--------------|
| **硅基流动 SiliconFlow**（⭐ 新手首选，一家全包） | [cloud.siliconflow.cn](https://cloud.siliconflow.cn)（手机号注册） | 控制台 → API 密钥：[cloud.siliconflow.cn/account/ak](https://cloud.siliconflow.cn/account/ak)（以官网为准） | 主对话 `llm`（`provider: siliconflow`）。同一把 key 会自动复用到看图、语音、生图/生视频云端兜底；也可复制到 `llm_memory` / `llm_emotion` 等高级节 | 新号常送体验额度，另有若干小模型长期免费；以官网为准 |
| **DeepSeek 官方** | [platform.deepseek.com](https://platform.deepseek.com) | 左侧 API keys：[platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)（以官网为准） | 工具判断 `llm_tool`（`provider: deepseek`，默认模板即用它）；也可作主 `llm` | 无长期免费额度，但价格很低、充值门槛小 |
| **Kimi / Moonshot** | [platform.moonshot.cn](https://platform.moonshot.cn)（现已跳转 platform.kimi.com，两个域名都通） | 控制台 → API Key 管理（以官网为准） | 主 `llm` 或后台任务 `llm_background`（`provider: kimi`，base_url `https://api.moonshot.cn/v1`） | 新用户一般送小额体验金；以官网为准 |
| **NVIDIA NIM** | [build.nvidia.com](https://build.nvidia.com) | 任一模型页登录后点 **Get API Key**（以官网为准） | 主 `llm` 的免费备胎（`provider: nvidia`，base_url `https://integrate.api.nvidia.com/v1`） | 注册送免费调用额度；**近期稳定性一般**，建议只作备用而非主力 |
| **DMXAPI** | [www.dmxapi.cn](https://www.dmxapi.cn) | 控制台 → 令牌 / API 令牌页（以官网为准） | 云端生图备选（`dall-e-3`，见第 6.1 节）；也可作 `llm`（`provider: dmxapi`，base_url `https://www.dmxapi.cn/v1`，可代理 GPT/Claude） | 聚合平台，按量充值；以官网为准 |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) | 主 `llm`（`provider: openrouter`，base_url `https://openrouter.ai/api/v1`） | 有一批带 `:free` 后缀的免费模型（限流）；国内直连可能不稳定，以官网为准 |

**只想最快跑起来**：选第一行硅基流动，注册 → 拿 key → 把 key 交给安装向导（或按下文 [3.2 节](#32-key-填到哪) 手填），完事。

---

## 1. 为什么推荐云端

| 对比 | 云端 API（本文） | 本地大模型（[进阶指南](LOCAL_ADVANCED.md)） |
|------|------------------|------------------------|
| 上手 | 填一把 key 即可，5 分钟 | 下载几 GB ~ 几十 GB 模型、装环境 |
| 硬件 | 任何电脑都行，不吃显卡 | 需要较强的 N 卡显存 |
| 费用 | 按量付费，通常有免费额度起步 | 免费，但要电费 + 硬盘 + 时间 |
| 维护 | 服务商替你升级模型 | 自己下模型、自己修 |

**结论**：先用云端把白跑通，享受完整体验；等你确实想省钱 / 离线 / 玩定制，再按进阶指南把某几项换成本地。两者可以混着用（比如聊天走云端、画图走本地）。

---

## 2. 一张表看懂：哪个功能要哪家服务

下表列出白的每个"智能功能"分别需要哪家云端服务、以及大致的额度 / 收费概念。

> 免费额度 / 价格由各服务商随时调整，**以官网当日为准**，这里只给"数量级"的心理预期。

| 功能 | 对应配置节 | 推荐云端服务 | 说明 / 额度概念 |
|------|-----------|--------------|-----------------|
| **主对话**（日常聊天，最核心） | `llm` | 硅基流动 / DeepSeek / 任一 OpenAI 兼容商 | 唯一"必填"的一把 key。新号常有赠送额度可先白嫖 |
| **工具判断**（要不要调工具） | `llm_tool` | DeepSeek / 硅基流动 | 需要强推理，DeepSeek 便宜好用 |
| **记忆分析**（提炼值得记的事） | `llm_memory` | 硅基流动 | 留空则记忆提取降级 |
| **情感分析**（读情绪、调语气） | `llm_emotion` | 硅基流动 | 留空则情感联动减弱 |
| **看图 / 看屏幕**（视觉理解） | `llm_vision` | 硅基流动（Qwen3-VL 系列） | 留空时可自动复用主 `llm` 的硅基流动 key |
| **后处理 / 检测 / 后台任务** | `llm_postprocess` / `llm_detect` / `llm_background` | 任一 | 全留空也不影响主聊天 |
| **生图**（文生图 / 改图） | `image`（走 role LLM 的 key） | **硅基流动**（`Qwen/Qwen-Image`）或 **DMXAPI**（`dall-e-3`） | 不配则先试本地 ComfyUI，再降级到这两家 |
| **生视频**（文生视频 / 图生视频） | 走 SiliconFlow key | **硅基流动 Wan2.2**（`Wan-AI/Wan2.2-T2V-A14B` / `I2V-A14B`） | 视频较贵、较慢，按需用 |
| **语音合成 TTS**（白开口说话） | `tts.fallback_*` | **硅基流动 CosyVoice2**（`FunAudioLLM/CosyVoice2-0.5B`） | 本地 GPT-SoVITS 没装时的云端兜底 |
| **语音识别 ASR**（听懂你说的话） | `asr` | **硅基流动 SenseVoice**（`FunAudioLLM/SenseVoiceSmall`） | 本地 faster-whisper 没装时走云端 |

**你会发现：硅基流动一家几乎全包**（对话、看图、生图、生视频、语音合成、语音识别）。所以最省心的做法就是——**先在硅基流动拿一把 key，绝大多数功能就都活了**。

---

## 3. 重点推荐：硅基流动（一家全包）

硅基流动（SiliconFlow，`api.siliconflow.cn`）是一个聚合平台，用**同一把 API key**就能调它上面的对话大模型、视觉模型、生图、生视频、语音合成、语音识别。对新手来说，"一把 key 打通所有功能"是最低的心智负担。

### 3.1 注册与拿 key

1. 打开硅基流动官网 [cloud.siliconflow.cn](https://cloud.siliconflow.cn) 并注册账号（手机号即可）。
2. 登录后进入控制台，找到 **API 密钥 / API Keys** 页面（[cloud.siliconflow.cn/account/ak](https://cloud.siliconflow.cn/account/ak)，以官网为准）。
3. 点击**新建 API 密钥**，复制生成的一串以 `sk-` 开头的字符串。**这就是你的 key**，妥善保存（页面通常只完整显示一次）。
4. 新账号一般带一点赠送额度，可以先不充值直接试。

> 除了硅基流动，你也可以用 **DeepSeek**（`api.deepseek.com`）、**NVIDIA NIM**、**Moonshot / Kimi**、**OpenRouter**、**DMXAPI**、或任何"OpenAI 兼容"的服务。白的 LLM 适配器是通用的，只要给对 `api_key` / `model` / `base_url` 三样就行。

### 3.2 key 填到哪

有两种填法，任选其一（效果一样）：

<!-- 2026-07-03 便捷化文档：把控制面板法提为首选，手改 conf.yaml 作进阶备选 -->
**填法 A（推荐，最省心）— 用控制面板填，不碰配置文件**

先随便把白 `Start.bat` 启动一下（还没填 key 也能启动），然后在桌宠上按 `Ctrl+,` 打开控制面板 → 进 **LLM配置**页 → 把各通道的 `api_key` / `model` / `base_url` 填进去 → 点保存 → 点『重启后端』按钮。面板会自动写回 `conf.yaml`，**全程无需命令行、无需手动编辑文件**。填完还能顺手点每个通道旁的【测试连通】确认（见 [3.3 节](#33-怎么验证通了控制面板测试连通)）。

**填法 B（进阶备选）— 直接改 `conf.yaml`**

想手动控制的话，先从模板复制一份自己的配置（只改 `conf.yaml`，**不要动 `conf.default.yaml` 模板**）：

```bash
copy conf.default.yaml conf.yaml     # Windows CMD / PowerShell
# cp conf.default.yaml conf.yaml     # Git Bash
```

打开 `conf.yaml`，把主对话这一节 `llm` 填成你的：

```yaml
llm:
  provider: "siliconflow"                       # 服务商标识（自定义即可）
  api_key: "sk-你复制的那串"                     # ← 必填！
  model: "deepseek-ai/DeepSeek-V3.2"            # 该服务商支持的模型名
  base_url: "https://api.siliconflow.cn/v1"     # 该服务商的 API 地址
  temperature: 0.7
  max_tokens: 2048
```

> `conf.yaml` 已被 `.gitignore` 忽略，**绝不会被提交到 Git**，你的密钥安全。配置是"深合并"的：`conf.yaml` 里只写你想改的项，其余自动走 `conf.default.yaml` 的默认值。

> 两种填法效果完全一样，写进的都是同一个 `conf.yaml`。新手建议用填法 A（面板），出问题也好排查。

### 3.3 怎么验证通了（控制面板"测试连通"）

控制面板的 LLM 设置页，每个通道旁边都有一个 **【测试连通】** 按钮（批 6 加入）。点它会用你当前填的参数发一次 **1-token 探活**请求：

- 通了：显示成功和大致耗时（毫秒）。
- 不通：显示错误原因（key 错、模型名不对、余额不足、网络不通等），照着改。

> 这是判断"key 到底填对没有"最快的方式，**填完先点一下测试连通**再启动，能省很多排查时间。

命令行侧也可以跑一次首次运行自检：

```bash
python scripts/first_run_check.py
```

看到"主 LLM 密钥已填写""关键依赖齐全"即可。

---

## 4. "最小可用"路径：先跑起来，再逐步加

**你不需要一开始就填满所有 key。** 最小可用只要**一把主对话 LLM key**：

1. 只填 `llm` 一节（如上 3.2）。
2. 启动（`Start.bat`）。
3. 现在白就能**桌面文字聊天**了。

其它 7 个分角色模型（`llm_tool` / `llm_memory` / `llm_emotion` / `llm_vision` / `llm_postprocess` / `llm_detect` / `llm_background`）此时可以先**留空**。如果主 `llm` 用的是硅基流动，看图、语音、生图/视频云端兜底会自动复用这把 key；记忆提取、情感联动、后台任务等更高级能力再按需单独填。

想要更细的能力隔离或更稳的后台功能时，再**逐个把对应节填上 key**即可。用硅基流动的话，通常把同一把 key 填到多个节就行（模型名按上表选）。

> 升级路线建议：主聊天（硅基流动时已含看图/语音/生图云端兜底）→ 加 `llm_memory`（让白记住你）→ 加 `llm_emotion` / `llm_background`（情感与后台任务更稳）→ 按需单独改 `llm_vision` 模型。

---

## 5. 分角色模型：想让更多功能工作时逐个填

以硅基流动一把 key 打通为例，`conf.yaml` 可以这样写（其余字段走默认即可）：

```yaml
llm:
  provider: "siliconflow"
  api_key: "sk-你的key"
  model: "deepseek-ai/DeepSeek-V3.2"
  base_url: "https://api.siliconflow.cn/v1"

llm_memory:
  provider: "siliconflow"
  api_key: "sk-你的key"
  model: "deepseek-ai/DeepSeek-V3.2"
  base_url: "https://api.siliconflow.cn/v1"

llm_emotion:
  provider: "siliconflow"
  api_key: "sk-你的key"
  model: "deepseek-ai/DeepSeek-V3.2"
  base_url: "https://api.siliconflow.cn/v1"

llm_vision:
  provider: "siliconflow"
  api_key: "sk-你的key"
  model: "Qwen/Qwen3-VL-8B-Instruct"      # 看图模型；与默认模板一致
  base_url: "https://api.siliconflow.cn/v1"
```

> 想给不同角色挑不同家（比如工具判断用 DeepSeek），把那一节的 `provider` / `base_url` / `model` 换成对应服务商的即可。每节含义的逐条详解见 [CONFIG.md](CONFIG.md) 的"LLM 多角色"部分。

---

## 6. 生图 / 生视频 / 语音的云端配置

这几项也都能纯云端跑，**不用装 ComfyUI / GPT-SoVITS**。

### 6.1 生图（云端）

白画图时是**三级降级**：先试本地 ComfyUI → 再试 DMXAPI → 最后试硅基流动。你**不装 ComfyUI**时，只要配了云端 key，白会直接用云端画：

- **硅基流动**：用 `Qwen/Qwen-Image` 模型，走你已经填的硅基流动 key（ASR/TTS 同款 key，会自动复用）。
- **DMXAPI**（可选备选）：用 `dall-e-3`，需要在 DMXAPI 注册拿一把它的 key。

> 生图的 key 默认从你已配置的角色 LLM 里自动扫一把 SiliconFlow 密钥复用——也就是说，**只要有一节 LLM 用了硅基流动，云端生图通常就能用**。DMXAPI 是额外可选项，想要它的画风时再配。

### 6.2 生视频（云端 Wan2.2）

文生视频 / 图生视频走**硅基流动 Wan2.2**：

- 文生视频：`Wan-AI/Wan2.2-T2V-A14B`
- 图生视频：`Wan-AI/Wan2.2-I2V-A14B`

用的是你的硅基流动 key。视频生成**较慢、较贵**（每条要排队等几十秒到几分钟），建议按需使用。

### 6.3 语音合成 TTS（云端 CosyVoice2）

白没装本地 GPT-SoVITS 时，说话走**硅基流动 CosyVoice2** 兜底。相关配置在 `tts` 节：

```yaml
tts:
  fallback_provider: "siliconflow"
  fallback_api_key: ""                                  # 留空=自动扫一把 SiliconFlow key
  fallback_model: "FunAudioLLM/CosyVoice2-0.5B"
  fallback_voice: "speech:neuro-sama:..."               # 兜底音色 ID（预置或自定义克隆）
```

> `fallback_api_key` 留空时，后端会**自动从已配置的角色 LLM 里扫一把硅基流动密钥**复用，所以通常不用重复填。

### 6.4 语音识别 ASR（云端 SenseVoice）

听你说话走**硅基流动 SenseVoice**。相关配置在 `asr` 节：

```yaml
asr:
  provider: "siliconflow"
  api_key: ""                            # 留空=自动扫一把 SiliconFlow key
  model: "FunAudioLLM/SenseVoiceSmall"
```

---

## 7. 常见问题（FAQ）

<!-- 2026-07-03 便捷化文档：重启后端点面板按钮即可，不用命令行 -->
**Q：key 填了怎么不生效？**
A：配置在后端**启动时读入并缓存**，改完 key 要**重启后端**才生效。**重启不用命令行**——控制面板右下角就有『重启后端』按钮，点一下即可（用面板填 key 的话，保存后顺手点它就生效）。改完先点【测试连通】确认，再重启。

**Q：提示余额不足 / 额度用完？**
A：去对应服务商控制台充值，或换一家还有免费额度的服务商（把该节 `provider` / `base_url` / `model` 换掉即可）。

**Q：某个模型突然报错、说找不到 / 已下架？**
A：服务商会下架旧模型。项目的模型自检会告警；把该节 `model` 换成服务商当前在售的同类模型名即可。

**Q：填了 key，白还是不说话 / 看不了图？**
A：如果主 `llm` 用硅基流动，一把 key 会自动复用到语音、看图、生图/视频云端兜底；如果主 `llm` 是别家，就需要按第 6 节给 `tts` / `asr` / `llm_vision` 单独补硅基流动或兼容服务配置。补完后【测试连通】+ 重启。

**Q：我想省钱 / 离线 / 自己训练白的专属声音？**
A：那就上本地大模型——见 [LOCAL_ADVANCED.md](LOCAL_ADVANCED.md)。云端和本地可以混用。

---

**下一步**：
- 想更省钱 / 离线 / 定制 → [本地大模型进阶指南 LOCAL_ADVANCED.md](LOCAL_ADVANCED.md)
- 配置项逐条详解 → [CONFIG.md](CONFIG.md)
- 从零安装 → [INSTALL.md](INSTALL.md)
