import pytest

from white_salary.core.initiative import InitiativeConfig, InitiativeEngine
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, ToolCall, ToolResult


class FakeJudgeLLM(LLMInterface):
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[Message] = []

    async def chat_completion(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        self.messages = messages
        return self.response

    async def chat_completion_stream(self, messages, temperature=0.7, max_tokens=2048):
        yield ""

    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        return "", []

    async def process_tool_results(
        self,
        messages: list[Message],
        tool_results: list[ToolResult],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        return ""


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_initiative_speaks_when_judge_allows() -> None:
    clock = FakeClock()
    llm = FakeJudgeLLM(
        '{"action":"speak","reason":"topic open","prompt":"你刚刚说到那个计划，我还挺好奇后面怎么弄。"}'
    )
    engine = InitiativeEngine(
        llm=llm,
        config=InitiativeConfig(first_delay_seconds=1),
        clock=clock,
    )

    pending_id = engine.record_turn("我今天想整理插件", "这个可以慢慢来。")
    assert pending_id is not None
    clock.now += 1.1

    decision = await engine.evaluate_if_due(pending_id)

    assert decision.should_speak
    assert "计划" in decision.prompt
    assert engine.pending_id is None
    assert "User last message" in llm.messages[-1].content


async def test_initiative_wait_reschedules() -> None:
    clock = FakeClock()
    llm = FakeJudgeLLM('{"action":"wait","reason":"too soon","delay_seconds":12}')
    engine = InitiativeEngine(
        llm=llm,
        config=InitiativeConfig(first_delay_seconds=1, max_waits=1),
        clock=clock,
    )

    pending_id = engine.record_turn("继续聊这个", "嗯，我听着。")
    assert pending_id is not None
    clock.now += 1.1

    decision = await engine.evaluate_if_due(pending_id)

    assert decision.action == "wait"
    assert decision.delay_seconds == 12
    assert engine.pending_id == pending_id
    assert engine.seconds_until_due(pending_id) == pytest.approx(12)


async def test_initiative_invalid_json_stays_silent() -> None:
    clock = FakeClock()
    engine = InitiativeEngine(
        llm=FakeJudgeLLM("not json"),
        config=InitiativeConfig(first_delay_seconds=1),
        clock=clock,
    )

    pending_id = engine.record_turn("你觉得呢", "我觉得可以。")
    assert pending_id is not None
    clock.now += 2

    decision = await engine.evaluate_if_due(pending_id)

    assert decision.action == "silence"
    assert engine.pending_id is None


async def test_initiative_cancel_makes_pending_stale() -> None:
    clock = FakeClock()
    engine = InitiativeEngine(
        llm=FakeJudgeLLM('{"action":"speak","prompt":"还在吗"}'),
        config=InitiativeConfig(first_delay_seconds=1),
        clock=clock,
    )
    pending_id = engine.record_turn("先这样", "好。")
    assert pending_id is not None

    engine.cancel_pending()
    clock.now += 2
    decision = await engine.evaluate_if_due(pending_id)

    assert decision.action == "silence"
