"""
White Salary - 超强AI智能体

这是项目的主包。所有源代码都在这个包下面。
项目采用六边形架构（Hexagonal Architecture），分为三层：
  - core/       核心域（纯业务逻辑，不依赖任何外部技术）
  - adapters/   适配器层（具体技术实现，如OpenAI、Whisper等）
  - infrastructure/  基础设施层（服务器、配置、日志等）
"""

__version__ = "0.1.10"
