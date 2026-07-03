"""推理工具 — 深度思考（真调辅助LLM通道做多步推理）。"""
from ._helpers import tool, P, S, I


# 2026-07-02 审计修复（批2）：reasoning/deep_reasoning 是提示词复读空壳（批2下架）。
# 2026-07-03 工具实现（批9）：两者合并实现为 deep_think（真调辅助LLM推理），
# 旧2名保持移除（函数体保留备查，不再导出）。
@tool("reasoning", "分步推理（展示思考过程）", P(question=S("问题", True)))
async def reasoning(question: str = "") -> str:
    return f"[推理] {question}\n请按步骤思考并展示推理过程。"


@tool("deep_reasoning", "深度多步推理（复杂问题）",
      P(question=S("问题", True), steps=I("推理步骤数")))
async def deep_reasoning(question: str = "", steps: int = 5) -> str:
    return (
        f"[深度推理] {question}\n"
        f"请按{steps}个步骤深入分析:\n"
        + "\n".join(f"{i+1}. [推理步骤{i+1}]" for i in range(steps))
    )


# ================================================================
# 2026-07-03 工具实现（批9）：deep_think——真调辅助LLM做深入推理
# ================================================================

# 深度思考助手的系统提示（独立通道，不占主模型上下文）
_DEEP_THINK_SYSTEM = "你是深度思考助手，逐步推理给出结论与依据。"


def _load_conf() -> dict:
    """读项目根目录 conf.yaml（从模块位置推导绝对路径，不依赖 CWD）。"""
    import yaml
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[5]
    return yaml.safe_load((project_root / "conf.yaml").read_text(encoding="utf-8")) or {}


def _pick_reasoning_channel(conf: dict) -> tuple[dict, str]:
    """
    选推理用的LLM通道：优先 llm_background（后台专用、不抢对话额度），
    没配则用 llm_postprocess 兜底。

    Returns:
        (通道配置dict, 通道名)：都没配时返回 ({}, "")
    """
    for section in ("llm_background", "llm_postprocess"):
        channel = conf.get(section) or {}
        if channel.get("api_key") and channel.get("base_url") and channel.get("model"):
            return channel, section
    return {}, ""


@tool("deep_think", "深度思考——复杂问题/多步推理/需要仔细想清楚时用：把问题交给专门的推理通道逐步分析，返回推理结论与依据供参考。当用户说「帮我仔细想想」「认真分析一下」「这个问题好复杂」时调用",
      P(question=S("要深入思考的问题（把必要的背景信息也写进来）", True)))
async def deep_think(question: str = "") -> str:
    question = (question or "").strip()
    if not question:
        return "请提供要深入思考的问题"

    try:
        conf = _load_conf()
    except Exception as e:
        return f"深度思考失败：配置读取失败（{e}）"

    channel, channel_name = _pick_reasoning_channel(conf)
    if not channel:
        return (
            "深度思考失败：辅助推理通道未配置。"
            "请在 conf.yaml 的 llm_background（或 llm_postprocess）节"
            "配置 api_key/base_url/model"
        )

    # 现场构造辅助LLM适配器（一次性使用，用完关闭连接，不常驻）
    from white_salary.adapters.llm.openai_compatible import OpenAICompatibleAdapter
    from white_salary.core.interfaces.types import Message, MessageRole

    adapter = OpenAICompatibleAdapter(
        api_key=str(channel["api_key"]),
        base_url=str(channel["base_url"]),
        model=str(channel["model"]),
        timeout=100.0,  # registry 超时表给 deep_think 120秒，内层留20秒余量
    )
    try:
        reply = await adapter.chat_completion(
            [
                Message(role=MessageRole.SYSTEM, content=_DEEP_THINK_SYSTEM),
                Message(role=MessageRole.USER, content=question),
            ],
            temperature=0.3,   # 推理要稳，不要发散
            max_tokens=1500,
        )
    except Exception as e:
        return f"深度思考失败：推理通道调用出错（{e}）"
    finally:
        try:
            await adapter.close()
        except Exception:
            pass  # 关闭失败不影响已拿到的结果

    if not (reply or "").strip():
        return "深度思考失败：推理通道没有返回内容"
    return f"【深度思考结果】（由辅助推理通道 {channel_name} 生成，供你融合参考）\n{reply.strip()}"


# 2026-07-03 工具实现（批9）：只导出 deep_think（reasoning/deep_reasoning 旧名保持移除）。
TOOLS = [fn._tool_def for fn in [deep_think]]
