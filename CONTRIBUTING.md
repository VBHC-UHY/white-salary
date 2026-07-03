# 参与 White Salary 开发

欢迎参与！这份文档带你熟悉项目结构、搭好开发环境、跑通测试，并说明这个项目的代码规范。
版本变化见 **[CHANGELOG.md](CHANGELOG.md)**。

---

## 一、项目结构导览（六边形架构）

后端在 `src/white_salary/` 下，遵循**六边形架构（Hexagonal / Ports & Adapters）**：核心业务逻辑不依赖任何具体技术，所有外部能力通过接口接入、可插拔替换。

```
src/white_salary/
├── core/              ← 纯业务逻辑（不碰任何第三方 SDK）
│   ├── agent/         对话引擎 chat_agent.py（三种回复模式 + 并行工具流）
│   ├── memory/        记忆系统（全项目最大子系统，5 层 + 自动发现的扩展模块）
│   ├── affinity/      好感度管理（11 级）
│   ├── personality/   人格管理（读 prompts/system_prompt.txt）
│   ├── emotion/       情感（并入 memory 的部分）
│   ├── filter/        内容安全过滤
│   ├── qzone/         QQ 空间社交逻辑
│   ├── social/        社交行为统一
│   ├── plugins/       插件系统（发现/沙箱/市场/开发者）
│   ├── services/      画像学习 / 记忆整理 / 向量检索 / 健康检查 / LLM 自检
│   └── interfaces/    ← 适配器抽象接口（"先定接口"就在这里）
│
├── adapters/          ← 技术实现（可插拔，实现 core/interfaces 的接口）
│   ├── llm/           OpenAI 兼容适配器（支持 13+ 提供商）+ factory
│   ├── asr/ tts/ vad/ vision/   语音识别 / 合成 / VAD / 视觉
│   ├── platform/      qq_adapter / qzone_api / bilibili_live 等平台接入
│   └── tools/         function-calling 工具 + comfyui / 浏览器 / 代码执行
│
├── infrastructure/    ← 服务器 / 配置
│   ├── server/        app.py / websocket_handler.py / qq_handler.py / settings_api.py
│   └── config/        loader.py（深合并）/ models.py（Pydantic 配置模型）
│
└── utils/             文本 / 音频 / 异步小工具
```

前端在 `frontend/`（Electron + PixiJS + Live2D），主进程 `main.js`，设置面板 `settings.html` + `js/settings.js`。

**依赖装配**：没有 DI 容器，所有依赖在 `run_server.py` 里**手工装配**——想看"谁依赖谁、启动时发生什么"，读 `run_server.py` 是最快的。

> ⚠️ 诚实提示：`infrastructure/` 下的 `session/` `events/` `pipeline/` `logging/` 目前是空壳（相关逻辑硬编码在 handler 里），`adapters/avatar/` `adapters/storage/` 也是空的。这些是未实现的解耦层，见本文档"项目结构导览"。

---

## 二、搭开发环境

参考 [docs/INSTALL.md](docs/INSTALL.md)，开发时额外装 dev 工具：

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"     # 装 pytest / ruff / mypy / pre-commit
```

> Python 版本：`pyproject.toml` 声明 `>=3.11`，但项目实测在 3.10 上也能跑（测试基线即在 3.10 通过）。新代码请避免用 3.11 独有语法，以保持对 3.10 的兼容。

后端运行**必须**设 `PYTHONPATH=src`（因为源码在 `src/` 布局下）：

```bash
set PYTHONPATH=src  &&  python run_server.py --debug     # CMD
$env:PYTHONPATH="src" ; python run_server.py --debug     # PowerShell
```

复制配置：`copy conf.default.yaml conf.yaml`，至少填一把主 LLM 密钥（见 [docs/CONFIG.md](docs/CONFIG.md)）。

---

## 三、跑测试

```bash
set PYTHONPATH=src  &&  python -m pytest tests -q
```

- **基线：605 个测试全绿**。提交前请确保测试数不减、无 FAILED。
- 新功能应配单元测试，放 `tests/` 下（`pytest` 配置见 `pyproject.toml`，已开 `asyncio_mode = auto`）。
- 只跑某个文件：`python -m pytest tests/test_xxx.py -q`。

代码检查（可选但推荐）：

```bash
ruff check src          # lint
ruff format src         # 格式化
mypy src                # 静态类型检查（strict 模式）
```

---

## 四、代码规范（铁律）

这个项目对代码质量要求很高，请严格遵守：

1. **全中文注释**：文件头 / 类 / 方法 / 关键逻辑都要写清楚，让非专业程序员也能看懂。
2. **完整类型注解**：所有函数参数和返回值都标类型（mypy strict）。
3. **先接口后实现**：新的 AI 组件先在 `core/interfaces/` 定接口，再在 `adapters/` 写实现——保证可通过配置切换。
4. **不裸吞异常**：不要写 `except Exception: pass`。要么处理、要么记日志、要么用自定义异常（继承 `WhiteSalaryError`）。
5. **异步优先**：所有 I/O 用 `async/await`。
6. **命名**：4 空格缩进，`snake_case`（变量/函数）、`PascalCase`（类）。
7. **不改变现有运行行为**：修 bug / 重构时，默认行为必须与改动前一致；把写死的值改成"可配置"时，默认值 = 原来的写死值。
8. **每处改动加日期注释**，说明原因，例如：`# 2026-07-03 XXX：<原因>`。

---

## 五、这个项目的特殊约定

- **占位功能是规划，不是垃圾**：代码里有些"未实现的占位"（面板按钮、工具空壳）是作者有意留下的功能规划。改进它们请**实现**，不要直接删除 UI 或函数。
- **密钥安全**：真实密钥只放本地的 `conf.yaml`（已 gitignore），绝不写进任何会提交的文件。测试脚本用的密钥放 `scripts/providers.json`（gitignore），仓库里只提交 `.example` 模板。
- **无 git 历史时代的备份习惯**：改动前做备份是本项目的传统，重要重构前建议先复制一份到本地 `backups/`（该目录已 gitignore）。
- **版本管理**：遵循 [语义化版本](https://semver.org/lang/zh-CN/)，变更记录进 [CHANGELOG.md](CHANGELOG.md)。

### 提交前检查清单

- [ ] `PYTHONPATH=src python -m pytest tests -q` 全绿，测试数不减
- [ ] 新功能配了单元测试
- [ ] 改动处加了日期 + 原因注释
- [ ] 没有把真实密钥 / 个人信息写进提交的文件
- [ ] 相关文档（README / docs）已同步更新
- [ ] 如发布新版本，更新了 CHANGELOG.md

