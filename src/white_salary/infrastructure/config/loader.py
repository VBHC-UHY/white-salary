"""
white_salary/infrastructure/config/loader.py

配置加载器。

负责从YAML文件加载配置，并合并默认配置和用户配置。

加载顺序：
  1. 先加载 conf.default.yaml（默认配置，所有配置项都有）
  2. 再加载 conf.yaml（用户配置，只包含用户想改的项）
  3. 用户配置覆盖默认配置
  4. 用 Pydantic 验证最终配置

这样用户只需要在 conf.yaml 里写想改的配置，其余自动用默认值。
"""

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from white_salary.core.exceptions import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigValidationError,
)
from white_salary.infrastructure.config.models import AppConfig


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    深度合并两个字典。

    和普通的 dict.update() 不同，这个函数会递归合并嵌套的字典，
    而不是直接覆盖整个子字典。

    比如：
        base     = {"server": {"host": "localhost", "port": 12400}}
        override = {"server": {"port": 8080}}
        结果     = {"server": {"host": "localhost", "port": 8080}}
        （host保留了默认值，port被覆盖了）

    2026-07-03 审计修复（批5）：新增"None 覆盖保护"——用户在 conf.yaml 里把某节
    写成空（如只写一行 `llm_tool:`）时，YAML 解析为 None，旧逻辑会拿 None 整节
    抹掉默认配置字典，下游对 None 调 .get() 抛 AttributeError 被吞、角色 LLM
    静默失效；现在跳过该覆盖并 warning 告警
    （依据 docs/audit-2026-07-02/config-audit.json）。

    参数:
        base:     基础字典（默认配置）
        override: 覆盖字典（用户配置）

    返回:
        合并后的新字典（不修改原字典）
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict):
            if isinstance(value, dict):
                # 两边都是字典，递归合并
                result[key] = _deep_merge(result[key], value)
            elif value is None:
                # 2026-07-03 审计修复（批5）：用户把整节留空（YAML解析为None），
                # 跳过覆盖、保留默认值，防止整节配置被 None 抹掉
                logger.warning(
                    f"配置节 '{key}' 在用户配置中为空(None)，"
                    f"已跳过覆盖并保留默认值（如需清空请显式写出各字段）"
                )
            else:
                # 默认值是字典但用户写成了标量——按旧行为直接覆盖，
                # 交给后续 Pydantic 校验报错（保持行为不变）
                result[key] = value
        else:
            # 直接覆盖
            result[key] = value

    return result


def _load_yaml_file(file_path: Path) -> dict[str, Any]:
    """
    加载一个YAML文件并返回字典。

    参数:
        file_path: YAML文件的路径

    返回:
        解析后的字典

    异常:
        ConfigFileNotFoundError: 文件不存在时抛出
        ConfigError: 文件格式错误时抛出
    """
    if not file_path.exists():
        raise ConfigFileNotFoundError(
            f"配置文件不存在: {file_path}",
            details={"path": str(file_path)},
        )

    try:
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # yaml.safe_load 对空文件返回 None
        if data is None:
            return {}

        if not isinstance(data, dict):
            raise ConfigError(
                f"配置文件格式错误，应该是一个YAML字典: {file_path}",
                details={"path": str(file_path), "type": type(data).__name__},
            )

        return data

    except yaml.YAMLError as e:
        raise ConfigError(
            f"YAML解析失败: {file_path}: {e}",
            details={"path": str(file_path), "error": str(e)},
        ) from e


def load_config(
    project_root: Path | None = None,
    default_config_name: str = "conf.default.yaml",
    user_config_name: str = "conf.yaml",
) -> AppConfig:
    """
    加载并合并配置文件，返回验证后的配置对象。

    这是配置系统的主入口函数。

    流程：
      1. 加载默认配置文件（必须存在）
      2. 尝试加载用户配置文件（可以不存在）
      3. 深度合并（用户配置覆盖默认配置）
      4. Pydantic 验证
      5. 返回 AppConfig 对象

    参数:
        project_root:       项目根目录（默认自动检测）
        default_config_name: 默认配置文件名
        user_config_name:    用户配置文件名

    返回:
        验证后的 AppConfig 配置对象

    异常:
        ConfigFileNotFoundError: 默认配置文件不存在时抛出
        ConfigValidationError: 配置校验失败时抛出
    """
    # 确定项目根目录
    if project_root is None:
        # 默认：从当前文件向上找到项目根目录
        # 当前文件在 src/white_salary/infrastructure/config/loader.py
        project_root = Path(__file__).parent.parent.parent.parent.parent

    logger.debug(f"项目根目录: {project_root}")

    # 第一步：加载默认配置（必须存在）
    default_path = project_root / default_config_name
    logger.debug(f"加载默认配置: {default_path}")
    default_data = _load_yaml_file(default_path)

    # 第二步：尝试加载用户配置（可以不存在）
    user_path = project_root / user_config_name
    user_data: dict[str, Any] = {}

    if user_path.exists():
        logger.debug(f"加载用户配置: {user_path}")
        user_data = _load_yaml_file(user_path)
    else:
        logger.debug(f"用户配置文件不存在（使用默认配置）: {user_path}")

    # 第三步：深度合并
    merged_data = _deep_merge(default_data, user_data)
    logger.debug(f"配置合并完成，共 {len(merged_data)} 个顶级配置项")

    # 第四步：Pydantic 验证
    try:
        config = AppConfig(**merged_data)
    except Exception as e:
        raise ConfigValidationError(
            f"配置校验失败: {e}",
            details={"error": str(e)},
        ) from e

    logger.info(
        f"配置加载成功: {config.system.name} v{config.system.version} "
        f"(debug={config.system.debug})"
    )

    return config
