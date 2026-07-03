# 更新日志（Changelog）

本项目版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)（`主版本.次版本.修订号`）：
- **修订号**（0.1.**x**）：bug 修复、小改进，不改变用法
- **次版本**（0.**x**.0）：新增功能，向后兼容
- **主版本**（**x**.0.0）：重大变更 / 里程碑

---

## [未发布 / 开发中] · 功能大项（发布时定为 0.2.0）

在 0.1.4 之后陆续加入的新功能（累积中，达到里程碑后打 0.2.0 标签）：

- **游戏对接接口**：`POST /api/game/event` + `GET /api/game/ping`——外部游戏（如 Aurora Forge）在打 Boss / 升级 / 收伙伴等事件时上报，白在桌面实时道喜/吐槽（fire-and-forget，穿透忙碌模式）。
- **插件热加载**：`on_message`/`on_reply` 钩子接进 QQ/桌面消息链路（此前定义了从不触发）；插件市场装完即生效不用重启；坏插件隔离 + 超时保护。
- **B 站直播弹幕互动**：连进直播间读弹幕、用白的人格回复（默认关闭；关键词触发 + 同用户 30 秒冷却防刷屏；发弹幕需先登录 B 站）。
- **声音一键训练**：控制面板"声音克隆"页新增"开始训练"按钮，放上音频即可触发 GPT-SoVITS 训练白的专属声音并看进度（需本机装 GPT-SoVITS）。
- **稳定性**：LLM 通道从不稳定的 NVIDIA 免费接口迁移到更稳定的供应商（默认模板配置更新）。

测试：677 个单元 + 集成测试全绿。

---

## [0.1.4] - 2026-07-03 · 云端 API 复用与发布说明修复

面向公开仓库下载用户的体验修复版：重点解决“主聊天已填硅基流动 key，但看图、语音、生图/视频仍像没配置”的割裂问题，并修复 GitHub Release 中文说明乱码。

### 修复
- **一把硅基流动 key 自动复用**：新增统一云端配置解析，主 `llm` 使用硅基流动时，自动复用到看图、语音 ASR/TTS、生图/生视频云端兜底。
- **媒体工具不再误用 key**：生图工具只把 DMXAPI key 发给 DMXAPI，不再把硅基流动 key 先错投到 DMXAPI 端点；SiliconFlow 和 DMXAPI 分开解析。
- **看图/截图/B站看视频/智能点击统一配置**：这些入口从合并后的配置读取 `llm_vision`，`llm_vision.api_key` 为空时可复用主硅基 key。
- **控制面板状态更准确**：状态页与“测试生图/视频”接口使用同一套 key 解析，不再显示未启用但实际可用，或点击后无反馈。
- **TTS 工厂旧字段修复**：`adapters/tts/factory.py` 改用当前 `tts.fallback_*` 字段，避免旧 `provider/voice` 字段被移除后潜在崩溃。
- **文档同步**：安装、配置、外部服务文档同步说明“一把硅基流动 key”的真实自动复用规则，并移除旧视觉模型推荐。

### 验证
- `python -m pytest tests/unit/test_cloud_config.py tests/unit/test_batch9_media_tools.py tests/unit/test_setup_wizard.py tests/unit/test_project_structure.py`
- `python -m pytest tests/unit/test_config_unify.py tests/unit/test_settings_api.py tests/unit/test_settings_di.py`

---

## [0.1.3] - 2026-07-03 · 服务器安装与依赖冲突修复

面向公开仓库下载用户的安装修复版：重点解决 `uv sync --extra all` / `white-salary[all]`
在 Python 3.13 环境下因 RVC 依赖链导致的 `numpy` 解析冲突，并补齐 Linux/服务器后端安装入口。

### 修复
- **RVC 依赖冲突拆分**：`rvc-python` 需要 `numpy<=1.25.3`，主项目需要 `numpy>=1.26.0`，因此不再放入 `singing-rvc` / `all` extra；RVC 改为文档说明的独立环境/外部服务接入。
- **Python 版本边界明确**：项目声明改为 `>=3.10,<3.13`，安装脚本优先使用 Python 3.12 / 3.11 / 3.10，避免新用户用 3.13 踩到未适配依赖。
- **Linux/服务器安装脚本**：新增 `install.sh`，支持只安装后端运行环境，并提供 `--with-memory` 安装 ChromaDB 长期记忆 extra。
- **B 站依赖说明修正**：文档和运行时提示统一为 `pip install -e ".[bilibili]"`，同时安装扫码登录和 Cookie 解密所需依赖。
- **安装检测补漏**：Windows 安装器和首次运行自检补入 `websockets`、`aiofiles` 检查。
- **Docker Compose 修复**：不再默认挂载缺失的 `conf.yaml`，避免 Linux 上把文件路径创建成目录。

### 验证
- `python -m pytest tests/unit/test_project_structure.py tests/unit/test_vad_fallback.py`
- `cmd /c "安装.bat /check"`
- `uv sync --extra all --dry-run`

---

## [0.1.2] - 2026-07-03 · 依赖审计与兜底修复

继续针对公开仓库下载后的“缺依赖 / 写死路径 / 版本不一致”做发布后审计。

### 修复
- **截图与看屏幕依赖补齐**：主依赖新增 `Pillow`、`mss`，避免截图/看屏幕/部分 ComfyUI GIF 合成在干净环境缺包。
- **服务调用依赖补齐**：主依赖新增 `httpx`，覆盖向量搜索等服务模块的直接 import。
- **前端缺包修复**：`frontend/package.json` 新增 `ws`，修复桌面端通过 Chrome DevTools Protocol 读取 B 站 Cookie 时缺 WebSocket 客户端的问题。
- **前端安全锁文件修复**：在不做 Electron 大版本迁移的前提下，锁文件升级 `axios`、`form-data`、`path-to-regexp`、`qs` 等可安全修复项。
- **Silero VAD 兜底**：`torch` 不再是顶层硬依赖；未安装 `torch` 或 Silero 模型加载失败时，自动降级到零依赖的 `EnergyVAD`。
- **本地 ASR 兜底**：`faster-whisper` 自动设备选择时，缺 `torch` 会使用 CPU 模式，不再误报成 faster-whisper 未安装。
- **可选依赖分组补齐**：新增 `desktop-control`、`bilibili`、`vad-silero`、`singing-rvc` extras，让启用桌面控制、B 站、Silero VAD、RVC 唱歌的人能按功能安装。
- **版本元数据同步**：Python 包版本、配置默认版本、前端 package 版本、文档示例同步到 `0.1.2`。

### 仍需后续单独处理
- `npm audit` 还剩 Electron 28 的高危公告；npm 建议升到 Electron 43，属于大版本迁移，需要单独做桌宠兼容验证后再升级。

### 验证
- `python -m pip install -e .`
- 安装脚本依赖 import 探测通过。
- Python import 依赖审计通过。
- 前端主进程依赖审计通过。
- `cmd /c "安装.bat /check"`
- `npm install --package-lock-only --ignore-scripts`
- 完整测试：`695 passed, 3 skipped`。

---

## [0.1.1] - 2026-07-03 · 安装与发布修复

面向公开仓库下载用户的修订版：不移动 `v0.1.0`，新增独立标签，方便从 GitHub Tags / Releases 下载到最新代码。

### 修复
- **一键安装隔离环境确认**：`安装.bat` 会创建并使用项目内 `.venv`，不会把依赖装进全局 Python。
- **依赖声明补齐**：主依赖包含 `openai`、`python-multipart`、`ddgs`、`yt-dlp`，新 clone 后 `pip install -e .` / `uv sync` 都会装到。
- **安装脚本漏检修复**：`安装.bat` 的“依赖已安装”判断补入 `yt_dlp`，避免旧环境只缺视频下载后端时被误判为已装齐。
- **Docker 构建修复**：Dockerfile 通过 `pyproject.toml` 安装依赖，并默认包含 `memory-vector`；新增 `.dockerignore`，排除 `.venv`、本地配置、日志、数据和前端依赖目录。
- **本地路径问题修复**：GPT-SoVITS 等本地工具路径改为从配置 / 环境变量解析，避免写死到某一台电脑的 `D:\AI_Tools\...`。
- **控制面板人设加载修复**：人设文件缺失时会从示例创建，控制面板不再因为提示词文件不存在而显示“无法加载分区”。
- **跨平台记忆与 QQ 兜底**：桌面 / QQ 对话会注入同一用户近期跨平台聊天上下文；QQ 回复链路增加异常兜底，降低“叫她发图或聊天但不回”的情况。

### 验证
- 干净临时虚拟环境验证 `pip install -e .` 必需依赖可导入。
- 验证 `pip install -e ".[memory-vector]"` 与 `pip check`。
- 完整测试：`692 passed, 3 skipped`。

---

## [0.1.0] - 2026-07-03 · 开源首发

首个公开版本。在一次系统性的全项目审计与修复后发布，核心链路全部可用、附带完整的新手安装体验。

### ✨ 新功能 / 亮点
- **AI 陪伴体「白」四形态**：Electron + Live2D 桌宠 / QQ 机器人（NapCat OneBot v11）/ QQ 空间 / B 站直播
- **多角色 LLM 架构**：主对话 + 工具/记忆/情感/视觉/后处理/检测/后台 共 8 个可独立配置的通道，支持多家供应商混搭，未配置的通道自动回退主通道
- **五层记忆系统**：短期 / 长期（向量检索）/ 核心档案 / 知识图谱 / 重要记忆，跨平台共享，按用户隔离
- **语音对话**：真流式 TTS（边生成边说）、可打断、语音输入（ASR）、情绪驱动语速
- **Live2D 表情系统**：情绪联动表情、自动眨眼、口型同步
- **120 个实用工具**：提醒（到点真通知）、忙碌/静默模式、看图、发文件、下载视频、联网搜索、深度推理、生图/生视频、QQ 互动等
- **好感度 + 情感系统**：陌生人→家人的关系演进，影响主动搭话频率与语气
- **控制面板**：22 页图形化配置（LLM/语音/人设/记忆/表情/插件/QQ/B站…），改配置即时生效或一键重启
- **插件系统**：GitHub 插件市场，可拉取/安装/提交

### 🚀 新手体验
- **一键安装**：双击 `安装.bat` 自动检测环境、装依赖、复制配置、拉起向导
- **图形配置向导**：粘贴一把 API key 即可开始，内置连通性测试
- **[图文说明书](docs/快速上手.md)**：面向完全不懂编程的用户
- **完整文档**：安装 / 配置 / 外部服务 / 本地进阶 / 贡献指南

### 🛡️ 工程质量
- **启动自检**：8 个 LLM 通道 1-token 探活，坏通道醒目告警（防「模型下架静默失败」）
- **584 个单元 + 14 个集成测试**全绿
- **配置全外置**：密钥、路径、外部服务全部可配置，开箱默认走云端
- 记忆多用户隔离与数据安全防护、QQ 链路稳定性加固（重连退避、并发安全）

### 📦 需自备的外部资源（因版权/隐私未随仓库分发）
- LLM API Key（[各家注册链接](docs/EXTERNAL_SERVICES.md#providers)，一把硅基流动 key 即可起步）
- Live2D 模型（放入 `live2d_models/`，桌宠形象）
- NapCat（QQ 功能，[官方仓库](https://github.com/NapNeko/NapCatQQ)）
- 本地大模型（进阶：ComfyUI 生图 / GPT-SoVITS 声音克隆，见 [本地进阶指南](docs/LOCAL_ADVANCED.md)）
- 角色人设（复制 `prompts/system_prompt.example.txt` 写自己的角色）

---

<!-- 下一个版本占位：功能大项（游戏对接 / 插件热加载 / B站直播 / 声音一键训练 / 真终端 / 桌面视觉）完成后发布 0.2.0 -->
