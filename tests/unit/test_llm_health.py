"""
llm_health（LLM 通道启动自检）的单元测试。

覆盖：正常通道、报错通道、超时通道、混合批量检查、None 通道跳过。
（2026-07-02 审计修复批1新增）
"""

import asyncio

from white_salary.core.services.llm_health import (
    check_all_llm_channels,
    check_llm_channel,
)


class _FakeOkLLM:
    """探活成功的假 LLM 适配器。"""

    async def chat_completion(self, messages, temperature=0.7, max_tokens=2048) -> str:
        return "好"


class _FakeBrokenLLM:
    """模拟"模型被下架"的假 LLM 适配器（对应审计发现的 404 场景）。"""

    async def chat_completion(self, messages, temperature=0.7, max_tokens=2048) -> str:
        raise RuntimeError("404 page not found")


class _FakeSlowLLM:
    """模拟"上游卡死不响应"的假 LLM 适配器。"""

    async def chat_completion(self, messages, temperature=0.7, max_tokens=2048) -> str:
        await asyncio.sleep(30)
        return "慢"


async def test_ok_channel_reports_healthy():
    """正常通道应返回 (名字, True, 空串)。"""
    name, ok, reason = await check_llm_channel("llm_memory", _FakeOkLLM())
    assert name == "llm_memory"
    assert ok is True
    assert reason == ""


async def test_broken_channel_reports_reason():
    """报错通道应返回 False 且原因里带上异常信息（如 404）。"""
    name, ok, reason = await check_llm_channel("llm_memory", _FakeBrokenLLM())
    assert ok is False
    assert "404" in reason


async def test_timeout_channel_reports_timeout():
    """超时通道应返回 False 且原因标明超时（不应抛异常）。"""
    name, ok, reason = await check_llm_channel("llm_detect", _FakeSlowLLM(), timeout=0.1)
    assert ok is False
    assert "超时" in reason


async def test_check_all_mixed_channels():
    """批量检查：坏通道进失败字典、好通道不进、None 通道被跳过。"""
    failed = await check_all_llm_channels(
        {
            "llm(主对话)": _FakeOkLLM(),
            "llm_memory": _FakeBrokenLLM(),
            "llm_vision": None,  # 未配置的通道应被跳过而不是报错
        }
    )
    assert set(failed.keys()) == {"llm_memory"}
    assert "404" in failed["llm_memory"]


async def test_check_all_empty_channels():
    """全部通道为 None 时应平静返回空字典。"""
    failed = await check_all_llm_channels({"llm_tool": None})
    assert failed == {}
