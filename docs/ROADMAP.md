# White Salary 项目计划书

更新时间：2026-07-04

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
