"""
white_salary/infrastructure/server/app.py

FastAPI 应用工厂。

创建并配置 FastAPI 应用实例，包含：
  - CORS 跨域配置
  - WebSocket 路由
  - 健康检查接口
  - 静态文件服务
"""

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from loguru import logger

from white_salary.infrastructure.config.models import AppConfig


def create_app(
    config: AppConfig,
    project_root: "Path | None" = None,
    runtime: "dict[str, Any] | None" = None,
) -> FastAPI:
    """
    创建并配置 FastAPI 应用。

    参数:
        config: 应用配置对象
        project_root: 项目根目录路径（用于设置API读写conf.yaml）
        runtime: 2026-07-03 审计修复（批5）：运行实例容器（desktop_agent /
                 qq_context_manager_getter / user_learning / memory_manager，
                 全部可为 None），透传给设置API路由，让面板的写操作能真正
                 触达运行中的实例（清空对话/触发学习等）

    返回:
        配置好的 FastAPI 实例
    """
    from pathlib import Path

    app = FastAPI(
        title="White Salary API",
        description="White Salary AI智能体后端服务",
        version=config.system.version,
    )

    # CORS 跨域配置（让前端能访问后端API）
    if config.server.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.server.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        logger.debug(f"CORS已启用, 允许来源: {config.server.cors_origins}")

    # 健康检查接口（用来确认后端是否在运行）
    @app.get("/health")
    async def health_check() -> dict:
        """健康检查：返回后端运行状态。"""
        return {
            "status": "ok",
            "name": config.system.name,
            "version": config.system.version,
        }

    # 2026-07-03 功能大项（批11）：注册游戏对接API路由（Aurora Forge 事件上报→白桌面播报）。
    # 不依赖 project_root/runtime，无条件挂载：游戏端只需 POST /api/game/event，
    # 内部把事件翻译成一句提示推给跨平台桥，播报链路（websocket_handler 轮询）已存在。
    from white_salary.infrastructure.server.game_api import create_game_router
    app.include_router(create_game_router())
    logger.debug("Game API registered at /api/game")

    # 注册设置API路由（控制面板用）
    if project_root:
        from white_salary.infrastructure.server.settings_api import create_settings_router
        # 2026-07-03 审计修复（批5）：透传运行实例容器（设置面板依赖注入）
        settings_router = create_settings_router(Path(project_root), runtime=runtime)
        app.include_router(settings_router)
        logger.debug("Settings API registered at /api/settings")

        # 表情包静态文件服务
        sticker_dir = Path(project_root) / "data" / "sticker"
        if sticker_dir.exists():
            from fastapi.staticfiles import StaticFiles
            app.mount("/sticker", StaticFiles(directory=str(sticker_dir)), name="sticker")
            logger.debug(f"Sticker static files at /sticker/")

    logger.info("FastAPI 应用已创建")
    return app
