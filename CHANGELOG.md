# 更新日志（Changelog）

本项目版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)（`主版本.次版本.修订号`）：
- **修订号**（0.1.**x**）：bug 修复、小改进，不改变用法
- **次版本**（0.**x**.0）：新增功能，向后兼容
- **主版本**（**x**.0.0）：重大变更 / 里程碑

---

## [0.1.10] - 2026-07-13 · 跨端直送、工具回执与错误收敛

这个修订版修复 QQ、桌面端和工具循环之间的反馈与重复回执问题，不改变现有启动方式、安装路径或平台配置。

### 修复与改进
- **跨端消息不再自问自答**：QQ、提醒等已经写好的文本通过 `direct` 模式原样送到桌面并合成语音，不再重新交给主模型改写；游戏事件等语义输入继续使用 `event_prompt`。
- **真实投递确认**：桌面桥只在 WebSocket 下发成功后确认直送消息；事件提示只有主模型完成后才确认，失败仍可由可靠队列处理。
- **工具成功不重复报幕**：发送语音、表情包和跨端消息成功后静默结束；内部工具协议和原始回执不会直接显示给用户。
- **错误提示收敛**：自动任务的相同 LLM 错误在 30 秒内合并，并增加短暂冷却；用户主动重试仍会显示本轮真实结果。
- **用户画像兼容结构化输出**：字典、列表和标量字段先规范化再去重，修复画像学习的 `unhashable type: 'dict'`。
- **单一人格边界**：始终由一个白负责回复，独立 `tool_llm` 只负责工具规划；只读工具可并行，副作用工具串行。

### 验证
- Windows Python 全量测试：`926 passed, 16 skipped`。
- 定向回归测试：`118 passed`。
- Electron 前端消息控制测试：`7 passed`。
- 本版本在从 `origin/main` 派生的独立工作树中完成，没有带入作者机器的固定路径、启动脚本或本地服务配置。

---

## [0.1.9] - 2026-07-12 · 服务器安装、可移植工具路径与 QQ 离线补消息

这个版本集中处理公开仓库在不同电脑和 Linux 服务器上的安装、启动与重连问题。本地作者工作区仍可保留自己的机器专用配置，公开版本不再假设任何人的盘符或工具目录。

### 新增
- **Linux / 服务器一键向导**：`server-setup.sh` 可选择 Python、创建项目 `.venv`、安装依赖、写入基础配置，并可选安装 systemd 服务；`--check` 只检查环境，不安装或改写文件。
- **公开版本地工具路径面板**：控制面板可配置 NapCat、GPT-SoVITS、ComfyUI、CosyVoice、Wav2Lip 和 ffmpeg。配置立即刷新，启动失败会显示实际原因，不再表现为“点了没反应”。
- **QQ 断线补消息**：NapCat 重新连接后会读取最近联系人和消息历史，把遗漏事件按时间顺序重新送入正常 QQ 消息链，而不是调用一个功能缩水的独立回复器。

### 修复与改进
- **服务器虚拟环境隔离**：安装器只使用项目 `.venv` 安装依赖，不污染系统 Python；支持 Python 3.10-3.12，并能识别“有 Python 但缺少 `ensurepip` / `pythonX.Y-venv`”的服务器环境。
- **Windows 安装器加固**：`/check` 会验证 `venv`、`ensurepip`、现有 `.venv` 和项目文件；`uv` 检查不再自动下载 Python，无法确认身份的非空 `.venv` 会拒绝递归删除。
- **服务器服务管理**：项目路径含空格时也能生成正确的 systemd 配置；在 WSL、容器或没有运行 systemd 的系统中会给出真实提示，不会假装服务已启动。
- **NapCat 启动路径**：Windows 可从环境变量、`conf.yaml`、控制面板或项目内 `NapCat/` 解析启动脚本；Linux 服务器明确使用单独部署的 OneBot 桥接，不尝试执行 Windows `.bat`。
- **离线消息幂等**：持久保存扫描游标和已处理消息 ID，短暂断线也会扫描；只有完整成功后才推进游标，处理失败的消息可在下次重连重试，并避免重复回复。
- **正常链路复用**：补回的离线消息继续经过黑白名单、群聊接话判断、上下文、好感度、插件、工具调用和真实发送回执。
- **安装与配置说明重排**：README、安装、配置、本地工具和快速上手文档分别说明 Windows 桌面版与 Linux 服务器版，减少新手混用命令和路径。

### 验证
- Windows Python 全量测试：`916 passed, 16 skipped`。
- Electron 前端消息控制测试：`4 passed`；`settings.js` 语法检查通过。
- WSL / Linux 安装器测试：`15 passed`，`install.sh` / `server-setup.sh` 语法检查和只读环境诊断通过。
- Windows `安装.bat /check` 通过，确认检查模式不执行安装。
- 公开文件绝对路径、作者账号和敏感信息扫描通过；真实依赖下载仍取决于目标服务器的 DNS 和软件源可用性。

---

## [0.1.8] - 2026-07-12 · 对话运行时、云端能力与安装器稳定性

本版本在保留现有桌面端与 QQ 入口行为的前提下，加入可恢复的 Agent Runtime v2 旁路基础设施，并集中修复工具、插件、云端语音、图片生成降级和 Windows 安装流程。

### 新增
- **Agent Runtime v2（旁路接入）**：新增会话 Actor、任务状态机、SQLite 事件日志、取消令牌和可靠投递队列；QQ 与桌面端写入统一任务账本，但暂不替换现有实时处理器。
- **跨端可靠投递**：QQ 以 NapCat 返回的真实 `message_id` 作为成功回执，桌面端以 WebSocket `done` 帧为完成条件；支持重试、幂等和未知状态核对。
- **云端声音克隆**：设置面板可上传短参考音频，创建、选择和删除 SiliconFlow 自定义音色；本地 GPT-SoVITS 权重保持独立。
- **运行时与安全文档**：新增 `docs/AGENT_RUNTIME_V2.md`，说明会话隔离、工具循环、记忆作用域、活动租约和迁移边界。

### 修复与改进
- **QQ 对话连续性**：完善会话池、活动窗口、图文合并、停止控制、工具循环和跨平台上下文记录，继续保留独立 `tool_llm` 的最终语义判断。
- **工具与插件安全**：补充平台、权限、可用性和副作用过滤；插件安装、热加载、沙箱执行和工具注册增加校验与回滚保护。
- **图片生成降级**：区分“发表情包”和“生成图片”，本地生成不可用时可按配置降级到云端，并返回可解释错误。
- **设置接口安全**：远程访问设置 API 可配置管理令牌；本机访问保持原有体验。
- **Windows 一键安装**：安装器始终使用项目 `.venv`，可从 `WS_PYTHON`、Windows `py`、`uv python find` 和系统 Python 中选择兼容的 3.10-3.12 解释器；`/check` 只检查、不改环境。
- **公开发布边界**：未配置本地 GPT-SoVITS、ComfyUI 等工具时给出配置提示或走云端降级，不回退到作者机器的固定路径。

### 验证
- Python 单元与集成测试全量通过。
- 前端消息控制器测试通过。
- `安装.bat /check` 通过，确认检查模式不会创建或修改虚拟环境。

---

## [未发布 / 开发中] · 功能大项（发布时定为 0.2.0）

在 0.1.5 之后陆续加入的新功能（累积中，达到里程碑后打 0.2.0 标签）：

- **安装文档校准**：README、快速上手、INSTALL、CONFIG、自检脚本提示统一强调 `.venv` 隔离安装、Python 3.10-3.12 边界、Linux `install.sh --with-memory` 用法，以及一把硅基流动 key 可复用到看图/语音/生图/生视频云端兜底。
- **服务器新手向导**：新增 `server-setup.sh`，面向 Linux / VPS / 云服务器，自动调用 `install.sh`、写入 `conf.yaml`、提示粘贴 SiliconFlow key、配置监听地址端口、可选长期记忆和 systemd 服务；文档明确区分 Windows 桌宠路线与服务器后端路线。
- **游戏对接接口**：`POST /api/game/event` + `GET /api/game/ping`——外部游戏（如 Aurora Forge）在打 Boss / 升级 / 收伙伴等事件时上报，白在桌面实时道喜/吐槽（fire-and-forget，穿透忙碌模式）。
- **插件热加载**：`on_message`/`on_reply` 钩子接进 QQ/桌面消息链路（此前定义了从不触发）；插件市场装完即生效不用重启；坏插件隔离 + 超时保护。
- **B 站直播弹幕互动**：连进直播间读弹幕、用白的人格回复（默认关闭；关键词触发 + 同用户 30 秒冷却防刷屏；发弹幕需先登录 B 站）。
- **声音一键训练**：控制面板"声音克隆"页新增"开始训练"按钮，放上音频即可触发 GPT-SoVITS 训练白的专属声音并看进度（需本机装 GPT-SoVITS）。
- **稳定性**：LLM 通道从不稳定的 NVIDIA 免费接口迁移到更稳定的供应商（默认模板配置更新）。

测试：677 个单元 + 集成测试全绿。

---

## [0.1.7] - 2026-07-04 · QQ 用户过滤模式与黑白名单面板

面向 QQ 机器人用户过滤的修复版：补齐黑名单、白名单、关闭过滤三种模式的控制面板入口，并让设置面板、工具和 QQ 运行实例使用同一套过滤器。

### 新增
- **用户过滤模式切换**：控制面板可在黑名单模式、白名单模式、关闭过滤之间切换；运行中的 QQ 服务会即时生效。
- **白名单管理**：控制面板新增白名单列表、加入和移除操作；白名单模式下只响应名单内用户，主人仍免检。
- **运行实例同步**：用户过滤 API 继续优先操作 QQ 运行中的 `UserFilter`，找不到运行实例时才回退到文件实例。

### 修复
- **好感度自动拉黑边界**：黑名单模式下保留好感度厌恶/仇恨自动软/硬拉黑；手动白名单或已验证用户不再被低好感度误自动拉黑。
- **家人号免过滤**：QQ 端家人号跳过用户过滤，避免白名单模式误伤第二个家人号。
- **手动拉黑语义**：控制面板手动拉黑默认永久，自动好感度拉黑仍保留软拉黑和升级逻辑。

### 验证
- `python -m pytest tests/unit/test_settings_panel_batch6.py tests/unit/test_tools_audit_fix.py tests/unit/test_smart_reply.py tests/unit/test_qq_stability.py -q`
- `python -m py_compile src/white_salary/core/memory/user_filter.py src/white_salary/infrastructure/server/settings_api.py src/white_salary/infrastructure/server/qq_handler.py`
- `node --check frontend/js/settings.js`

---

## [0.1.6] - 2026-07-04 · QQ 消息链路、当前会话续聊与插件市场 schema v2

面向日常聊天和插件生态的体验修复版：重点让 QQ 多段话先合并再理解，保留工具判断 LLM；桌面端新增“当前会话自然续聊”判断；插件系统补齐角色、观察钩子、工具候选边界和市场元数据。

### 新增
- **当前会话续聊判断**：新增 `InitiativeEngine`，在真实用户一轮聊天停下来后，由 LLM 判断是安静等待、轻轻追问，还是自然补一句想法。
- **插件角色**：插件元数据新增 `roles`，支持 `observer`、`interceptor`、`rewriter`、`tool_provider` 四类角色；旧插件未声明时保持原行为。
- **工具候选边界**：工具可声明平台、权限、服务依赖和副作用属性；系统只过滤明确不可用的工具，最终是否调用仍交给原本的 tool_llm 判断。
- **插件市场 schema v2**：市场同步/模板/安装支持角色、平台、权限、服务、依赖和资源声明，并新增插件市场说明文档。

### 修复
- **QQ 多段话处理**：QQ adapter 不再提前吞掉群聊消息，改为在消息合并后统一判断是否回复，避免“白 + 后续内容”被拆散理解。
- **QQ 表情包策略**：显式 `<sticker>` 必发，普通轻松回复按概率附带；报错、隐私、权限、长代码等严肃场景跳过。
- **消息缓冲唤醒**：缓冲达到上限时会立即唤醒等待中的 flush，减少多段话等待超时造成的迟钝感。
- **桌面端安全边界**：AI 自发续聊默认不调用工具、不操作电脑；真实用户消息仍走原工具链路。

### 验证
- `python -m pytest tests\unit\test_qq_stability.py tests\unit\test_qq_sticker_policy.py tests\unit\test_message_processing.py tests\unit\test_initiative_engine.py tests\unit\test_chat_agent.py tests\unit\test_tool_access_filter.py tests\unit\test_plugin_hooks.py tests\unit\test_plugin_market_paths.py -q`
- `python -m compileall -q src\white_salary\adapters\platform\qq_adapter.py src\white_salary\adapters\platform\sticker_policy.py src\white_salary\adapters\tools\registry.py src\white_salary\core\agent\chat_agent.py src\white_salary\core\initiative.py src\white_salary\core\message\processing.py src\white_salary\core\plugins\base.py src\white_salary\core\plugins\context.py src\white_salary\core\plugins\manager.py src\white_salary\core\plugins\market.py src\white_salary\infrastructure\server\qq_handler.py src\white_salary\infrastructure\server\settings_api.py src\white_salary\infrastructure\server\websocket_handler.py`
- `node --check frontend\js\settings.js`
- `node --check frontend\js\chat-controller.js`
- `git diff --check`

---

## [0.1.5] - 2026-07-03 · 插件目录统一与默认 Live2D 模型

面向公开仓库下载用户的体验修复版：重点解决“插件实际存在但插件市场/已安装列表看不到”的目录口径不一致问题，并把默认 Live2D 模型随源码提供，减少新手开箱后没有桌宠形象的困惑。

### 修复
- **插件目录统一**：插件市场“已安装”列表与启动时 `PluginManager` 使用同一套目录规则，统一识别 `plugins/`、`plugins/community/`、`plugins/builtin/`。
- **市场安装位置明确**：市场安装和模板创建默认写入 `plugins/community/<插件名>/`，继续兼容旧的 `plugins/<插件名>/` 布局。
- **内置插件保护**：`plugins/builtin/` 下的内置插件能显示、能禁用，但不会被卸载按钮直接删除。
- **默认 Live2D 模型随仓库提供**：公开仓库包含 `live2d_models/` 默认模型资源，下载后桌宠形象可开箱加载；Docker 构建仍排除该目录，避免服务器镜像变大。
- **文档同步**：README、快速上手、CONFIG、CHANGELOG 同步说明默认模型已附带，也保留用户自定义替换模型的路径。

### 验证
- `python -m pytest tests/unit/test_plugin_market_paths.py tests/unit/test_project_structure.py -q`
- `python -m py_compile src/white_salary/core/plugins/market.py`

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
