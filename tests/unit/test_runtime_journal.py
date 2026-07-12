from white_salary.core.runtime import (
    ConversationRef,
    InteractiveTaskJournal,
    RuntimeStore,
    TaskState,
)


def _journal(tmp_path):
    return InteractiveTaskJournal(RuntimeStore(tmp_path / "runtime.db"))


def test_interactive_task_records_response_and_real_completion(tmp_path):
    journal = _journal(tmp_path)
    task = journal.begin(
        ConversationRef("desktop", "owner", user_id="owner"),
        "hello",
        owner_id="owner",
    )

    assert task.should_process is True
    assert task.refresh().state == TaskState.WORKING

    task.response_ready("world", awaiting_delivery=True)
    assert task.refresh().state == TaskState.WORKING
    assert task.refresh().result_summary == "world"

    task.complete(receipt={"message_id": 42})
    assert task.refresh().state == TaskState.COMPLETED
    events = journal.store.list_events(task.id)
    assert any(event.payload.get("phase") == "response_ready" for event in events)
    assert any(event.payload.get("receipt", {}).get("message_id") == 42 for event in events)


def test_interactive_task_idempotency_does_not_restart_existing_work(tmp_path):
    journal = _journal(tmp_path)
    conversation = ConversationRef("qq", "group:100:user:200", scope="group")

    first = journal.begin(
        conversation,
        "first",
        idempotency_key="qq-message:123",
    )
    duplicate = journal.begin(
        conversation,
        "duplicate",
        idempotency_key="qq-message:123",
    )

    assert first.id == duplicate.id
    assert first.should_process is True
    assert duplicate.should_process is False
    assert duplicate.refresh().state == TaskState.WORKING
    assert any(
        event.event_type == "duplicate_input_ignored"
        for event in journal.store.list_events(first.id)
    )


def test_interactive_task_ambiguous_delivery_requires_reconciliation(tmp_path):
    journal = _journal(tmp_path)
    task = journal.begin(ConversationRef("qq", "private:200"), "hello")

    task.response_ready("world", awaiting_delivery=True)
    task.require_reconciliation("NapCat returned no message_id")

    assert task.refresh().state == TaskState.RECONCILIATION_REQUIRED
    assert "message_id" in task.refresh().error


def test_terminal_task_ignores_late_state_updates(tmp_path):
    journal = _journal(tmp_path)
    task = journal.begin(ConversationRef("desktop", "owner"), "hello")

    task.cancel("new input")
    task.complete("late reply")

    assert task.refresh().state == TaskState.CANCELLED
