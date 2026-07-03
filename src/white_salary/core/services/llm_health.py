"""
white_salary/core/services/llm_health.py

LLM 通道启动自检 — 服务启动后对所有已配置的 LLM 通道并发做一次 1-token 探活。

背景（2026-07-02 审计）：llm_memory 所配模型被上游下架（404）后，记忆提取与
用户学习静默失败了近一个月，日志里只有 WARNING 级散点记录，没人发现。
本模块让"模型下架 / 密钥失效 / 服务不可达"这类问题在启动时就被醒目暴露：
  - 所有通道并发探活（互不拖慢），单通道超时默认 45 秒
  - 结果汇总成一块醒目的日志横幅（失败的用 ERROR 级）
  - 探活在后台任务里跑，不阻塞服务启动、不阻塞任何请求
"""

import asyncio
from typing import Any

from loguru import logger

from white_salary.core.interfaces.types import Message, MessageRole


async def check_llm_channel(
    role_name: str,
    llm: Any,
    timeout: float = 45.0,
) -> tuple[str, bool, str]:
    """
    对单个 LLM 通道做一次 1-token 探活。

    参数:
        role_name: 通道名（如 "llm_memory"）
        llm:       LLM 适配器（需实现 chat_completion 方法）
        timeout:   单通道超时秒数（NVIDIA 等平台冷启动较慢，给足余量）

    返回:
        (通道名, 是否可用, 失败原因——可用时为空串)
    """
    try:
        await asyncio.wait_for(
            llm.chat_completion(
                messages=[Message(role=MessageRole.USER, content="ping")],
                temperature=0.0,
                max_tokens=1,
            ),
            timeout=timeout,
        )
        return (role_name, True, "")
    except asyncio.TimeoutError:
        return (role_name, False, f"超时（>{timeout:.0f}秒无响应）")
    except Exception as e:
        # 适配器层已把上游错误转成自定义异常，这里只需把原因带出来
        return (role_name, False, f"{type(e).__name__}: {e}")


async def check_all_llm_channels(
    channels: dict[str, Any],
    timeout: float = 45.0,
) -> dict[str, str]:
    """
    并发探活全部 LLM 通道，输出醒目的汇总日志。

    参数:
        channels: {通道名: LLM适配器}，值为 None 的通道自动跳过
        timeout:  单通道超时秒数

    返回:
        {通道名: 失败原因}，全部健康时为空字典
    """
    todo = {name: llm for name, llm in channels.items() if llm is not None}
    if not todo:
        logger.warning("[LLM自检] 没有可检查的LLM通道")
        return {}

    logger.info(f"[LLM自检] 开始探活 {len(todo)} 个LLM通道（后台进行，不影响使用）...")
    results = await asyncio.gather(
        *(check_llm_channel(name, llm, timeout=timeout) for name, llm in todo.items())
    )

    failed: dict[str, str] = {name: reason for name, ok, reason in results if not ok}
    ok_names = [name for name, ok, _ in results if ok]

    if ok_names:
        logger.info(f"[LLM自检] ✅ 正常 {len(ok_names)}/{len(todo)}: {', '.join(ok_names)}")
    if failed:
        # 用 ERROR 级横幅醒目输出，防止再次"静默瘫痪一个月"
        logger.error("=" * 60)
        logger.error(f"[LLM自检] ❌ {len(failed)} 个LLM通道不可用！依赖它们的功能会静默失败：")
        for name, reason in failed.items():
            logger.error(f"[LLM自检]   - {name}: {reason}")
        logger.error("[LLM自检] 请检查 conf.yaml 中对应通道的 model / api_key / base_url")
        logger.error("=" * 60)
    return failed
