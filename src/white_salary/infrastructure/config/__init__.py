"""
配置管理模块。

提供配置加载、验证和访问功能。

用法:
    from white_salary.infrastructure.config import load_config

    config = load_config()
    print(config.server.port)  # 12400
"""

from white_salary.infrastructure.config.loader import load_config
from white_salary.infrastructure.config.models import (
    AppConfig,
    AutoChatConfig,
    ExternalToolsConfig,
    FeaturesConfig,
    QQConfig,
    RoleLLMConfig,
)

# 2026-07-03 审计修复（批5）：导出新增的子配置模型，供消费方类型注解使用
# 2026-07-03 外部依赖优化（批8）：追加导出 ExternalToolsConfig
__all__ = [
    "load_config",
    "AppConfig",
    "RoleLLMConfig",
    "QQConfig",
    "AutoChatConfig",
    "FeaturesConfig",
    "ExternalToolsConfig",
]
