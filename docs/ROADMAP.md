# White Salary 项目计划书

更新时间：2026-07-14

这个文件用于记录项目当前结构、已完成事项、已知问题、下一步计划和验收结果。以后每次做完比较重要的功能或修复，都应该同步更新这里，避免任务做到一半后上下文丢失。

## 记录规则

- 重要功能、架构变化、Bug 修复、验收结果要写进本文件。
- 只影响发布说明的内容再同步到 `CHANGELOG.md`。
- 长期工作偏好或接手规则可以写进 Codex 记忆，但项目事实以本文件为准。
- 本地专用配置、公开仓库版本、服务器版本要分开记录，避免把本地死路径误改成通用路径，或把通用仓库误写成本地路径。
- 桌面端、QQ 端、插件系统、安装脚本尽量分提交处理，不要混在一个提交里。

## 当前结构快照

- `frontend/`：Electron 桌面端、Live2D、聊天输入、字幕/气泡、语音输入、持续监听、设置面板。
- `src/white_salary/core/`：对话 Agent、记忆、情绪、好感度、主动行为、插件核心、社交策略等核心逻辑。
- `src/white_salary/adapters/`：LLM、TTS、ASR、视觉、QQ/NapCat、B 站、工具和外部服务适配。
- `src/white_salary/infrastructure/server/`：FastAPI、WebSocket、QQ 服务入口、设置 API。
- `plugins/`：插件目录，包含内置插件、社区插件和本地插件。
- `docs/`：安装、配置、外部服务、本地进阶和项目计划。
- `tests/`：单元测试和部分集成测试。

## 已完成且应保持稳定的能力

- 桌面端可以通过 WebSocket 和后端聊天，支持逐句 TTS、Live2D 口型同步、说话字幕和气泡。
- 持续监听和长按说话都属于桌面端语音输入能力，提示信息不能影响 AI 正常说话字幕。
- QQ 端已经有 NapCat/OneBot 适配、群聊记录、智能回复判断、消息缓冲、工具调用、记忆写入、表情包管理等模块。
- 记忆系统已经存在跨会话、多层记忆、核心记忆、长期记忆、情绪和好感度联动。
- 插件系统已经有核心框架、市场、沙箱、安全执行器和官方插件入口。
- 安装脚本已经逐步改为虚拟环境/uv 管理方向，公开仓库版本应避免污染全局 Python 环境。

## 最近完成

### 2026-07-14 v0.1.11：公开版安装、启动与服务器健康检查加固

完成内容：
- Windows `安装.bat` 明确区分完整桌宠与 `/backend-only`；完整安装缺 Node.js 或 npm 失败时会停止，不再误报成功，所有 Python 依赖仍只装入项目 `.venv`。
- 公开版 `Start.bat` 改为读取配置端口、验证真实 `/health`、复用已健康服务，并在端口被其它程序占用时安全停止，不再结束未知进程。
- Linux `server-setup.sh` 首次配置必须有 API key，非本机监听自动生成管理令牌；systemd 启动后等待真实健康响应，失败时返回日志排查命令。
- Docker 后端显式监听 `0.0.0.0:12400` 并带健康检查；移除未被应用使用的独立 Chroma 容器，继续使用挂载的 `data` 保存内嵌向量库数据。
- ComfyUI 控制面板启动接口区分已在线、非 Windows、未配置、启动失败和超时，不再把所有错误统一显示成超时。
- README、INSTALL、快速上手和 CONFIG 已同步到 `0.1.11`，并把 Windows、Linux 服务器和 Docker 三条路线分开说明。

验证结果：
- Windows Python 全量测试：`930 passed, 19 skipped`。
- WSL / Linux 安装器行为测试：`18 passed`；两个 shell 安装脚本语法检查通过。
- Windows 安装与设置定向测试：`45 passed, 16 skipped`；Electron 前端测试：`7 passed`。
- `安装.bat /check` 实机通过，确认检查模式不创建环境、不安装依赖、不改配置。

发布边界：
- 本轮代码来自 `origin/main` 派生的独立公开工作树，不从作者本地工作区复制启动脚本、固定盘符或本机服务配置。
- 发布前仍需完成敏感信息/固定路径扫描、版本一致性测试、远端基线复核和 GitHub Release 校验。

### 2026-07-13 v0.1.10：跨端直送、工具回执与重复错误修复

问题：
- QQ 的 `push_to_desktop` 消息此前会被桌面桥重新包装成新提示，再交给主模型生成回复，可能形成白回答自己消息的反馈环。
- 桥接任务启动后就提前确认成功；如果主模型或 WebSocket 随后失败，消息仍可能被错误标成已处理。
- 工具后处理失败时可能把内部回执直接展示给用户；语音、表情包和跨端推送成功后还可能补一条多余确认。
- LLM 断线时自动任务会连续触发相同错误；用户画像合并结构化字段时还可能触发 `unhashable type: 'dict'`。

完成内容：
- 跨端队列区分 `direct` 和 `event_prompt`：QQ、提醒等已经写好的文本原样送到桌面并合成语音，不再调用 Agent；只有游戏事件等需要白理解的内容才进入主模型。
- 桥消息只在 WebSocket 文本真实下发后确认；事件提示只有主模型真实完成后确认，失败继续交给可靠队列重试或核对。
- `qq_send_voice`、`qq_send_sticker`、`push_to_desktop` 成功后静默结束，不再追加重复文字；内部工具回执不再作为聊天兜底文本。
- 自动 LLM 失败增加短暂冷却，前后端合并 30 秒内相同的自动错误；用户主动重试不受自动错误去重影响。
- 用户画像字段在合并前统一把字典、列表和标量规范化为稳定文本，再按顺序去重。
- 文档明确运行时只有一个白的主回复人格；`tool_llm` 只负责工具规划，互不依赖的只读工具可并发，副作用工具串行。

验证结果：
- Windows Python 全量测试：`926 passed, 16 skipped`。
- 定向回归（跨端、桌面流式、工具后处理、用户学习）：`118 passed`。
- Electron 前端消息控制测试：`7 passed`。
- `git diff --check` 通过；本轮未修改 `Start*.bat`、本地工具路径或机器专用启动行为。

仍需真实环境验收：
- 用真实 NapCat 从 QQ 触发 `push_to_desktop`，确认桌面只播一次原消息、QQ 不收到内部回执、断网时队列不会提前确认。
- 在真实 LLM 断线和恢复场景观察自动冷却与用户主动重试。

### 2026-07-12 v0.1.9：服务器安装、可移植工具路径与 QQ 离线补消息

完成内容：
- 重写 `install.sh` 和 `server-setup.sh`：Python 3.10-3.12 自动选择、项目 `.venv` 隔离、缺失 `ensurepip` / `pythonX.Y-venv` 的诊断与可选补齐、只读 `--check`、可选 systemd 服务。
- 加固 Windows `安装.bat`：检查模式验证虚拟环境能力但不下载/改写；旧 `.venv` 只有在身份可确认时才允许自动重建。
- 公开控制面板增加本地工具路径配置，统一解析 NapCat、GPT-SoVITS、ComfyUI、CosyVoice、Wav2Lip 和 ffmpeg；启动按钮改为返回后端真实结果，不再依赖作者机器盘符。
- NapCat 重连补消息改为持久化游标与消息 ID 去重，并将遗漏消息重新送入 `QQAdapter` 的正常消息入口，保留上下文、好感度、黑白名单、插件、工具与发送回执。
- README、INSTALL、CONFIG、LOCAL_ADVANCED 和快速上手区分 Windows 桌面宠物与 Linux 服务器后端，写清本地工具配置入口和 NapCat 部署边界。

验证结果：
- Windows Python 全量测试 `916 passed, 16 skipped`；前端测试 `4 passed`。
- WSL / Linux 安装器测试 `15 passed`，脚本语法、systemd 单元、只读检查、Python/venv 选择与权限边界均通过。
- Windows `安装.bat /check` 通过；公开文件绝对路径、作者账号与敏感信息扫描通过。
- 已重新对齐远端 `origin/main`，确认 v0.1.9 基于公开版 v0.1.8 开发并复跑关键验证。真实依赖下载仍取决于目标服务器的 DNS 和软件源可用性，发布后还需核对 PR、标签与 Release 指向同一提交。

发布边界：
- 这批公开改动在从 `origin/main` 派生的独立工作树中完成，不从 `D:\White Salary` 原样复制或上传机器专用启动路径。
- Windows 本地 `.bat` 启动器与 Linux 服务器后端是两条路线；服务器上的 NapCat / OneBot 桥接需要独立部署。

### 2026-07-12 v0.1.8：Agent Runtime v2 旁路、云端能力与安装器修复

完成内容：
- 增加 ConversationActor、TaskRuntime、SQLite 事件日志、取消令牌和可靠 Outbox，QQ 与桌面端接入同一任务账本；当前保持 sidecar，不接管原实时入口。
- QQ/NapCat 使用真实消息回执完成任务，桌面 WebSocket 使用 `done` 帧确认；补充幂等、重试、未知状态和重启恢复测试。
- 完善 QQ 会话池、活动租约、图文上下文、停止控制、工具调用和跨平台长期记忆衔接，保留现有 `tool_llm` 判断与好感度/黑白名单链路。
- 增加 SiliconFlow 云端声音克隆管理，并完善本地图片生成失败后的云端降级。
- 加固插件安装、热加载、沙箱、工具注册和设置 API 远程访问令牌。
- 重写 Windows 解释器选择逻辑：兼容 `py`、`uv` 与显式 `WS_PYTHON`，所有依赖安装到项目 `.venv`；`安装.bat /check` 保证只读检查。
- 公开版继续保持路径可配置；本地机器专用启动方式和固定路径不随公开版本发布。

验证结果：
- Python 全量测试、前端消息控制器测试和 Windows 安装器检查模式均通过。
- 发布前对工作树执行绝对路径、凭证、生成缓存和 Git 差异扫描。

后续：
- 在真实 QQ、桌面、断网重连和进程重启场景做回放与故障注入；通过前不把 Runtime v2 切为默认调度器。
- 继续补齐运行中追加要求、跨端任务接力和插件工具的端到端验收。

### 2026-07-03 插件市场路径一致性修复

问题：
- 插件核心代码、根目录插件、社区插件、内置插件的目录含义容易混在一起。
- 插件市场之前主要扫描根目录 `plugins/<id>`，没有和运行时发现规则完全对齐。
- 新建/下载插件如果直接放根目录，后续和内置/社区插件区分不够清楚。

处理：
- `PluginMarket` 增加统一的插件目录迭代逻辑，识别 `plugins/<id>`、`plugins/community/<id>`、`plugins/builtin/<id>`。
- 新建模板插件和市场安装插件默认进入 `plugins/community/`。
- 卸载逻辑支持 community 插件，并保护 builtin 插件不被删除。
- 安装列表返回插件来源和路径，方便面板显示与排查。

验收：
- 已新增 `tests/unit/test_plugin_market_paths.py`。
- 已运行 `python -m pytest tests/unit/test_plugin_market_paths.py -q`，结果 3 passed。
- 已运行 `python -m compileall -q src/white_salary/core/plugins/market.py`。

### 2026-07-03 桌面端语音反馈修复

问题：
- 持续监听点击后反馈不够明确，用户可能误以为没有反应。
- 长按说话在麦克风失败、录音太短、后端未连接、发送失败时，部分路径只写 console 或反馈不明显。
- 语音错误提示不应该复用 AI 说话字幕容器，避免影响字幕自动消失。

处理：
- 在 `frontend/js/chat-controller.js` 中增加聊天区系统消息提示。
- 持续监听显示“请求权限/校准噪声/已开启/失败原因”。
- 长按说话显示“环境不支持/麦克风失败/没录到声音/后端未连接/发送失败”。
- 不修改 `_updateSubtitle`、`_autoHideSubtitle`、`_scheduleHideAfterAudio` 这些 AI 说话字幕逻辑。

验收：
- 用户已确认桌面端持续监听和长按说话反馈恢复正常。
- 已运行 `node --check frontend/js/chat-controller.js`。

### 2026-07-04 当前会话续聊、插件角色和工具候选边界

问题：
- 现有 `auto_chat` 更偏“隔一段时间主动关心/随机话题”，不能解决用户当前聊天停顿后白自然接一句的问题。
- 插件目前主要是 `on_message` 抢答、`on_reply` 改写、`get_tools` 注册工具，缺少“只观察/学习但不抢答”的明确角色。
- 工具数量已经较多，后续插件继续提供工具时需要平台、权限、服务可用性边界，但不能把 tool_llm 判断替换成关键词硬判。

处理：
- 新增 `InitiativeEngine`，用于在真实用户一轮聊天结束后记录 `pending_continuation`，到期后由 LLM 判断 `silence/wait/speak`。
- 桌面 WebSocket 接入当前会话续聊：用户新消息/语音/图片会取消 pending；续聊通过统一 `_launch_reply` 下发，可被 interrupt 打断。
- 主动续聊默认 `allow_tools=False`，只自然接话，不主动调用工具或操作电脑；真实用户消息仍保留原 `tool_llm` 工具判断。
- 插件元数据新增 `roles`，兼容默认 `interceptor/rewriter/tool_provider`；新增 `observer` 钩子用于只观察消息、不抢答。
- QQ 和桌面消息链路都会给插件注入平台、群号、发送者等上下文；QQ observer 仍在“决定需要回复”之后执行，不绕过群聊回复门槛。
- `ToolRegistry` 增加 `ToolAccessContext` 和工具元数据：`platforms/requires_permission/requires_service/side_effect`。当前只过滤明确标注不可用/无权限的工具，未标注工具继续交给 tool_llm 判断。

验收：
- `python -m pytest tests\unit\test_initiative_engine.py -q`
- `python -m pytest tests\unit\test_plugin_hooks.py -q`
- `python -m pytest tests\unit\test_tool_access_filter.py tests\unit\test_chat_agent.py -q`
- `python -m pytest tests\unit\test_qq_stability.py tests\unit\test_qq_sticker_policy.py -q`
- `python -m pytest tests\unit\test_ws_streaming.py -q`
- `python -m pytest tests\unit\test_batch9_behavior.py tests\unit\test_tools_audit_fix.py -q`
- `python -m pytest tests\integration\test_core_links.py -q`

剩余观察：
- 需要真实桌面端观察续聊语气是否自然、是否过于频繁；必要时调 `first_delay_seconds` 和 LLM 判断提示。
- 需要逐步给高风险/平台专属工具补元数据标签，才能让工具候选过滤发挥更大作用。
- QQ 端当前先不主动续聊群消息，后续如要加，必须单独做群聊打扰度和主人私聊优先级策略。

### 2026-07-04 插件市场元数据兼容层

问题：
- 运行时插件已经开始区分 `interceptor/rewriter/tool_provider/observer`，但插件市场仍主要只认识 `plugin.py/config.json/plugins.json` 的旧字段。
- 第三方插件如果带素材、提示词或 README，旧同步逻辑不够明确，容易出现“代码上传了，资源没上传”或“市场列表看不出权限/类型”的问题。
- 旧插件、新型插件和复杂插件需要共存，不能为了新字段破坏老插件。

处理：
- `PluginMarket` 增加市场元数据规范化：旧插件缺 `roles/platforms/permissions/requires_service/assets/dependencies` 时自动补默认值。
- 市场提交和同步会写入 `schema_version=2`、插件角色、平台、权限、服务、依赖和资源声明。
- 市场同步会上传 `README.md`、`assets/`、`prompts/`、`docs/` 以及 `config.json` 中声明的资源文件，并同步更新 `plugins.json` 索引。
- 新建插件模板支持 `classic/interceptor/observer/rewriter/tool_provider` 类型；默认仍是旧式通用插件。
- 插件市场页面显示插件类型、平台、权限和服务要求。
- 新增 `docs/PLUGIN_MARKET.md` 说明插件市场路径、角色、元数据和安全边界。

验收：
- 已运行 `python -m pytest tests\unit\test_plugin_market_paths.py tests\unit\test_plugin_hooks.py -q`，结果 30 passed。
- 已运行 `python -m compileall -q src\white_salary\core\plugins\market.py src\white_salary\infrastructure\server\settings_api.py`。
- 已运行 `node --check frontend\js\settings.js`。
- 已运行 `git diff --check`，仅剩 Windows 换行转换提示，无实际 whitespace 错误。

剩余观察：
- 当前网页提交接口仍以 `plugin.py + config.json` 为主；完整目录上传适合走本地同步或后续单独做 ZIP 上传。
- 依赖只记录和展示，不自动安装，后续如要做“一键装依赖”必须加用户确认和安全提示。

### 2026-07-04 QQ 唤醒词、媒体合并和续聊闸门修复

问题：
- QQ 端唤醒词只写死识别少数形态，`白？`、`白！`、`，白`、前后空格等常见叫法不够稳。
- 群聊活跃窗口曾把“白刚参与过”近似当成“可以继续接话”，容易在用户和别人聊天、自言自语或换话题时乱插嘴。
- 主人/家人发图片或语音曾被 adapter 标成强制回复，导致没叫白也触发回复。
- 图片/表情包和文字的理解需要进入同一轮消息合并，否则模型容易看不到图片语义。
- QQ 发语音工具成功后不应该再补一句“语音已发送”。

处理：
- 新增 QQ 专用 `qq.wake_words` 配置和设置页入口；默认 `白`，匹配时自动允许前后空格和常见标点，只影响 QQ 端，不影响桌面端。
- `SmartReplyDecider` 改为配置化唤醒词，并新增 `SEMANTIC_CHECK`：活跃窗口只表示“候选续聊”，不再直接放行。
- `qq_handler.py` 在活跃窗口内调用 `llm_detect` 做“是否还在和白说话”的语义闸门；无检测模型时用保守分数兜底。
- 去掉“家人发图片/语音强制回复”，只保留“引用白消息”作为 adapter 级强制回复。
- 媒体消息经过 ASR/视觉理解后补进 QQ 群上下文，后续唤醒时能看到图片/表情含义。
- “别回/不许发消息/别说话”等明确停话指令在插件、工具、LLM 前硬拦截；主人发出时写入现有静默状态。
- 保留原时间上下文，只加 QQ 运行时回复约束：白知道时间，但不要无关地反复强调“现在是晚上/下午/几点”。
- `qq_send_voice` 成功时走内部静默完成标记，避免再向 QQ 发送“语音已发送”文字。

验收：
- `python -m pytest tests/unit/test_smart_reply.py -q`，12 passed。
- `python -m py_compile src/white_salary/core/smart_reply.py src/white_salary/infrastructure/server/qq_handler.py src/white_salary/core/services/startup_checker.py src/white_salary/core/agent/chat_agent.py src/white_salary/infrastructure/config/models.py run_server.py`。
- `python -m pytest tests/unit/test_qq_stability.py tests/unit/test_batch9_media_tools.py tests/unit/test_batch9_behavior.py -q`，112 passed。
- `python -m pytest tests/unit/test_bilibili_wiring.py tests/unit/test_settings_api.py -q`，38 passed。
- `git diff --check` 仅报告 Windows 换行提示，无实际 whitespace 错误。

剩余观察：
- 需要真实 QQ 群里观察 `llm_detect` 对“继续和白说话/转头和别人说话/自言自语”的判断是否够稳。
- 如果用户后续想加更多 QQ 唤醒词，只改 `qq.wake_words`，不要改桌面端逻辑。

### 2026-07-04 QQ 黑名单工具与运行实例对齐

问题：
- 本地 `data/memory/user_filter.json` 当前处于黑名单模式但名单为空，陌生人默认不会因为黑名单被拦。
- QQ 消息入口实际使用的是启动时注册到 `settings_api` 的持久化 `UserFilter`。
- 社交工具里的拉黑/解除/查看仍在调用旧接口，且没有优先拿 QQ 运行中的过滤器实例，容易出现“工具显示拉黑了，但 QQ 实际处理不是同一套”的错觉。
- 连续消息 3 秒防刷冷却、休息模式和群聊唤醒判断也可能让用户误以为是黑名单拦截，需要和黑名单问题分开排查。

处理：
- 为持久化 `UserFilter` 补回 `block/unblock/get_blocked_list` 兼容接口，旧工具不再因为接口缺失失效。
- 社交黑名单工具优先使用 `settings_api` 注册的 QQ 运行实例，找不到运行实例时才回退到默认持久化过滤器。
- `block_user`、`unblock_user`、`manage_blacklist` 改为操作 `add_to_blacklist/remove_from_blacklist`，确保面板、工具和 QQ 实际过滤一致。
- 新增测试覆盖旧接口兼容和社交工具操作运行实例。

验收：
- `python -m pytest tests/unit/test_settings_panel_batch6.py::TestUserFilterListBlacklist tests/unit/test_tools_audit_fix.py::TestSocialBlacklistTools -q`，3 passed。
- `python -m pytest tests/unit/test_tools_audit_fix.py tests/unit/test_settings_panel_batch6.py -q`，60 passed。
- `python -m py_compile src/white_salary/core/memory/user_filter.py src/white_salary/adapters/tools/builtin/social.py`。

剩余观察：
- 如果真实 QQ 仍出现“加好友后仍不回”或“陌生人私聊不回”，优先查看社交冷却、休息模式、唤醒词/语义闸门和 NapCat 事件类型，而不是先按黑名单处理。

### 2026-07-04 QQ 黑白名单模式与好感度过滤补齐

问题：
- `UserFilter` 核心已有 `blacklist/whitelist/off` 三种模式和好感度自动拉黑逻辑，但控制面板只暴露黑名单增删，没有白名单列表/移除和模式切换入口。
- 白名单数据虽然会持久化到 `data/memory/user_filter.json`，但设置 API 未返回，前端无法确认“开白名单/开黑名单/关闭过滤”是否真实生效。
- 好感度自动拉黑应该只在黑名单模式中作为安全兜底；手动信任的白名单用户不应被低好感度误拉黑。
- QQ 家人号列表可能不止一个，用户过滤不能只豁免第一个主人号。

处理：
- `UserFilter` 新增 `list_whitelist/remove_from_whitelist`，并统一规范化用户 ID。
- 黑名单模式下，手动白名单和已验证用户跳过好感度自动拉黑；好感度厌恶/仇恨仍会对普通用户触发软/硬拉黑。
- Settings API 新增 `POST /users/filter/mode`、`POST /users/filter/whitelist`、`DELETE /users/filter/whitelist/{user_id}`，并在 `GET /users/filter` 返回白名单、模式列表和统计。
- 控制面板用户管理页新增黑名单/白名单/关闭过滤模式切换、白名单增删、黑名单永久勾选；所有操作优先作用于 QQ 运行实例。
- QQ 端家人号跳过用户过滤，避免白名单模式误伤第二个家人号。
- 版本元数据更新为 `v0.1.7`。

验收：
- `python -m pytest tests/unit/test_settings_panel_batch6.py tests/unit/test_tools_audit_fix.py tests/unit/test_smart_reply.py tests/unit/test_qq_stability.py -q`，99 passed。
- `python -m py_compile src/white_salary/core/memory/user_filter.py src/white_salary/infrastructure/server/settings_api.py src/white_salary/infrastructure/server/qq_handler.py`。
- `node --check frontend/js/settings.js`。

剩余观察：
- 需要真实 QQ 测试三种过滤模式：黑名单模式默认回复但拦截名单/低好感用户；白名单模式只回白名单/家人；关闭过滤时不走用户过滤但仍保留休息、唤醒和社交冷却逻辑。

## 已知问题和待办

### P0 - QQ 消息处理流程重整

本轮完成（2026-07-03）：
- QQ 群聊不再由 `QQAdapter` 提前吞消息；adapter 只标记“引用白/主人发图或语音”等强制回复信号，实际是否回复改到 `qq_handler.py` 的消息合并之后判断。
- `MessageBuffer` 满上限时会唤醒等待中的 flush，不再只返回 `False` 但仍等超时。
- QQ 调用 `ChatAgent.chat_stream_with_tools()` 时会传入 `route_text`，工具路由和 tool_llm 判断只看当前合并后的用户话，主模型仍保留完整群上下文。
- 插件 `on_message(text, user_id)` 签名保持不变，同时可通过 `self.context.get_message_context()` 读取平台、群号、发送者等 QQ 上下文。
- QQ 表情包补成专用策略：显式 `<sticker>` 必发；普通轻松回复按概率附带；报错、权限、隐私、长回复、代码等严肃场景跳过。

目标：
- 按正常聊天逻辑重排 QQ 入口：消息归一化 -> 同人短时间连发合并 -> 是否回复判断 -> 忙碌/隐私/休息判断 -> Agent/工具 -> QQ 专属后处理 -> 发送和记录。
- 让多段话先合并再理解，减少一句一句乱回。
- 把 `family_qq`/主人身份正确传入智能回复判断。
- 表情包做成 QQ 专属策略：按情绪、上下文、冷却和概率决定是否带图，不影响桌面端。
- 给 QQ 消息合并、主人优先级、表情包策略补测试。

验证：
- `python -m pytest tests\unit\test_message_processing.py -q`
- `python -m pytest tests\unit\test_qq_stability.py tests\unit\test_qq_sticker_policy.py -q`
- `python -m pytest tests\unit\test_batch9_behavior.py::TestHintInjection -q`
- `python -m pytest tests\unit\test_plugin_hooks.py -q`
- `python -m pytest tests\unit\test_smart_reply.py -q`
- `python -m pytest tests\unit\test_tools_audit_fix.py -q`
- `python -m pytest tests\unit\test_ws_streaming.py -q`
- `python -m pytest tests\integration\test_core_links.py -q`

剩余观察：
- 真实 QQ 群内还要观察多用户插话、长时间活跃群、主人连续发图/语音时的回复频率。
- 表情包概率目前是 QQ 后处理策略，后续可继续接入更细的情绪分类和标签选择。
- 多用户同话题合并仍然是后续增强项；本轮先修“同一用户连发先合并再理解”。

### P0 - 项目计划和任务状态持续记录

目标：
- 每次完成重要修复或功能后更新本文件。
- 记录“做了什么、为什么做、怎么验收、还有什么风险”。
- 未完成任务不要只留在聊天记录里。

### P1 - 主动感知和授权分级

需求方向：
- 用户授权后，桌面端可以在特定触发条件下观察屏幕和用户行为，例如键盘/鼠标活动、应用切换、游戏/工作场景。
- 不是持续无差别识别，而是按授权等级、触发条件和隐私规则采样。
- 观察结果进入记忆系统，用于理解用户习惯、偏好和常用场景。
- AI 可以根据上下文判断是否互动，不能看到事件就乱说话。

建议实现阶段：
- 阶段 1：授权 UI 和配置分级，只记录事件元数据，不自动操作。
- 阶段 2：屏幕识别摘要和记忆写入，加入隐私过滤和用户可见日志。
- 阶段 3：非打扰式互动策略，支持“别说话/忙碌/继续看着”等用户意图。
- 阶段 4：用户离开电脑后的低风险自主行为，例如写小文档、整理计划、生成图片草稿。
- 阶段 5：远程协助和手机端气泡互动，需要额外授权和更严格安全边界。

安全边界：
- 默认关闭，首次使用必须解释风险并分级授权。
- 聊天软件、私密页面、密码/支付/身份信息默认不读取、不操作。
- 自主操作必须有暂停、停止、日志和回滚入口。

### P1 - 跨平台记忆一致性

问题：
- 桌面端和 QQ 端需要共享对用户的长期理解。
- 不同平台的上下文注入、会话记录、用户身份映射要统一，否则会出现“QQ 聊过但桌面不知道，桌面聊过但 QQ 不知道”。

方向：
- 明确用户身份映射表：桌面用户、QQ 私聊、QQ 群内用户。
- 统一写入核心记忆和长期记忆，但保留平台来源。
- 对话上下文短期窗口按平台隔离，长期事实跨平台共享。

### P1 - 插件系统路径和市场一致性

现状：
- 插件核心位于 `src/white_salary/core/plugins/`。
- 仓库根目录也有 `plugins/`，适合作为实际插件存放目录。
- 需要统一内置插件、社区插件、本地插件、市场下载插件的目录规则。

方向：
- 核心框架继续留在 `src/white_salary/core/plugins/`。
- 第三方/社区/用户安装插件优先放在根目录 `plugins/community/`。
- 内置插件可放 `plugins/builtin/` 或代码内官方插件入口，但要和市场显示规则一致。
- 插件市场、插件管理器、文档和测试必须认同同一套路径。

新增约定：
- `observer`：只观察消息、记录/学习，不抢答。
- `interceptor`：可在 LLM 前抢答。
- `rewriter`：可改写 AI 最终回复。
- `tool_provider`：可向 ToolRegistry 注册工具。
- 旧插件不写 `roles` 时默认保持原行为；新插件如果只想观察，应显式写 `roles=["observer"]`。

### P2 - 服务器安装和 Windows 安装说明

方向：
- Windows 新手优先使用 `安装.bat`。
- Linux/服务器优先使用 `install.sh` 或 Docker Compose。
- 两边都应创建独立虚拟环境，不污染全局 Python。
- README 和安装文档要明确区分 Windows 桌宠使用、服务器部署、可选本地模型和云端 API。

## 下一步建议

1. 观察真实 QQ 使用反馈，重点看多段话合并、工具误触发、表情包概率是否自然。
2. 同步补插件路径规则的测试和文档，避免市场和运行时识别不一致。
3. 再进入主动感知方案设计，先做授权和事件记录，不直接做自主操作。
4. 每完成一项，都更新本文件并单独提交。
