"""
基础设施层（Infrastructure）

这一层提供项目运行所需的基础设施支持。

子模块：
  - config/    配置管理（读取YAML、Pydantic验证）
  - server/    Web服务器（FastAPI + WebSocket）
  - session/   会话管理（每个用户连接的状态隔离）
  - pipeline/  处理管道（音频管道、文本管道）
  - logging/   日志系统
  - events/    事件系统（模块间解耦通信）
"""
