# 插件市场说明

本文件说明 White Salary 插件市场的目录、元数据和兼容规则。它面向想写插件、提交插件、或者排查插件安装问题的人。

## 插件放在哪里

运行时会扫描这些位置：

| 位置 | 用途 |
|------|------|
| `plugins/community/<插件ID>/plugin.py` | 市场下载、新建模板、第三方插件默认位置 |
| `plugins/builtin/<插件ID>/plugin.py` | 项目自带内置插件，不建议用户删除 |
| `plugins/<插件ID>/plugin.py` | 本地覆盖/旧版兼容位置 |

市场仓库里的标准位置是：

```text
plugins/<插件ID>/plugin.py
plugins/<插件ID>/config.json
plugins/<插件ID>/assets/...      # 可选
plugins/<插件ID>/prompts/...     # 可选
plugins/<插件ID>/README.md       # 可选
plugins.json                    # 市场索引
```

下载安装到本地时，会进入 `plugins/community/<插件ID>/`，不会和内置插件混在一起。

## 插件角色

旧插件不用写 `roles`，默认仍按老逻辑运行：

```json
["interceptor", "rewriter", "tool_provider"]
```

新插件可以显式声明角色：

| 角色 | 作用 |
|------|------|
| `interceptor` | 可通过 `on_message()` 抢答/拦截用户消息 |
| `rewriter` | 可通过 `on_reply()` 改写 AI 最终回复 |
| `tool_provider` | 可通过 `get_tools()` 注册工具 |
| `observer` | 只通过 `on_observe()` 观察/学习消息，不抢答、不改写、不注册工具 |

例子：

```python
from white_salary.core.plugins.base import Plugin, PluginMeta


class MoodObserverPlugin(Plugin):
    meta = PluginMeta(
        name="mood_observer",
        description="只观察聊天气氛，不抢答",
        roles=["observer"],
    )

    async def on_observe(self, text, user_id="", metadata=None):
        return None
```

## `config.json` 推荐字段

```json
{
  "schema_version": 2,
  "id": "example_plugin",
  "name": "Example Plugin",
  "cn_name": "示例插件",
  "version": "1.0.0",
  "author": "your-name",
  "description": "一句话说明插件做什么",
  "category": "工具",
  "roles": ["tool_provider"],
  "platforms": ["qq", "desktop"],
  "permissions": ["owner"],
  "requires_service": ["napcat"],
  "dependencies": {
    "python": ["httpx"]
  },
  "assets": [
    "assets/icon.png",
    "prompts/system.md"
  ],
  "enabled": true
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `roles` | 插件参与哪类运行时钩子；不写则按老插件兼容 |
| `platforms` | 插件适合的平台，常见值是 `desktop`、`qq`、`bilibili`、`qzone`、`server`、`all` |
| `permissions` | 插件需要的权限提示，例如 `owner`、`admin`、`filesystem`、`network` |
| `requires_service` | 插件依赖的外部服务，例如 `napcat`、`comfyui`、`siliconflow` |
| `dependencies` | 额外依赖声明，只做展示和安装前提示，不会偷偷自动安装 |
| `assets` | 需要随插件一起下载/同步的资源文件，路径必须在插件目录内部 |

## 提交和同步规则

- 网页表单提交：会上传 `plugin.py`、`config.json`，并更新 `plugins.json`。
- 本地同步到 GitHub：会上传 `plugin.py`、`config.json`、`README.md`、`assets/`、`prompts/`、`docs/` 以及 `config.json` 中声明的 `assets`。
- 下载插件：会下载 `plugin.py`、`config.json` 和 `assets` 中声明的资源文件。
- 依赖不会自动安装。插件如果需要额外 pip 包，必须在市场信息里写明，安装时由用户确认后再处理。

## 安全边界

- `assets` 不允许绝对路径，也不允许 `..` 路径穿越。
- `plugins/builtin/` 是内置插件目录，市场卸载会保护它。
- 插件默认仍会走沙箱和安全执行器；坏插件超时或异常时，不应该拖垮主聊天链路。
