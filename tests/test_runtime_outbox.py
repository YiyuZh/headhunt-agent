import pytest
from langgraph.types import Command

from app.runtime.outbox import (
    LangGraphOutboxHandler,
    RuntimeNotReadyError,
    feishu_card_payload_to_human_approval,
    feishu_payload_to_initial_state,
)


class FakeGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, input_value, config):
        self.calls.append((input_value, config))
        return {"status": "ok", "input": input_value}


class FakeGraphFactory:
    def __init__(self):
        self.agent_harness = object()
        self.graph = FakeGraph()

    def create_headhunter_war_room_graph(self):
        return self.graph


class FakeResumeReadyGraphFactory(FakeGraphFactory):
    def __init__(self):
        super().__init__()
        self.action_gate = object()
        self.action_executor = object()


def test_feishu_payload_to_initial_state_extracts_text_without_full_payload() -> None:
    payload = {
        "header": {"event_type": "im.message.receive_v1", "event_id": "evt_1"},
        "event": {
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"请做三省六部会审"}',
            },
            "sender": {"sender_id": {"open_id": "ou_1"}},
        },
    }

    state = feishu_payload_to_initial_state(payload)

    assert state["source"] == "feishu"
    assert state["source_ref"] == "om_1"
    assert state["user_input"] == "请做三省六部会审"
    assert state["feishu_context"] == {
        "event_type": "im.message.receive_v1",
        "event_id": "evt_1",
        "message_id": "om_1",
        "chat_id": "oc_1",
        "open_id": "ou_1",
    }
    assert "payload" not in state


def test_langgraph_outbox_handler_dispatches_to_real_graph_invoke() -> None:
    graph = FakeGraph()
    handler = LangGraphOutboxHandler(
        graph=graph,
        use_postgres_checkpointer=False,
        allow_minimal_runtime=True,
    )

    handler.dispatch_graph(
        {
            "header": {"event_type": "im.message.receive_v1", "event_id": "evt_1"},
            "event": {
                "message": {
                    "message_id": "om_1",
                    "content": '{"text":"请为 AI 平台负责人岗位做人岗筛选"}',
                }
            },
        }
    )

    state, config = graph.calls[0]
    assert state["user_input"] == "请为 AI 平台负责人岗位做人岗筛选"
    assert config["configurable"]["thread_id"] == state["thread_id"]
    assert handler.last_result["status"] == "ok"


def test_langgraph_outbox_handler_does_not_fake_dispatch_success_by_default() -> None:
    handler = LangGraphOutboxHandler(graph=FakeGraph(), use_postgres_checkpointer=False)

    with pytest.raises(RuntimeNotReadyError, match="AgentHarness"):
        handler.dispatch_graph({"event": {"message": {"message_id": "om_1"}}})


def test_langgraph_outbox_handler_dispatches_when_factory_has_real_harness() -> None:
    factory = FakeGraphFactory()
    handler = LangGraphOutboxHandler(
        graph_factory=factory,
        use_postgres_checkpointer=False,
    )

    handler.dispatch_graph({"event": {"message": {"message_id": "om_1", "content": "hello"}}})

    assert factory.graph.calls[0][0]["source_ref"] == "om_1"


def test_card_payload_to_human_approval_extracts_resume_command_payload() -> None:
    payload = {
        "event": {
            "operator": {"open_id": "ou_1"},
            "action": {
                "value": {
                    "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
                    "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
                    "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
                    "idempotency_key": "idem-1",
                    "decision": "approve",
                }
            },
        }
    }

    approval = feishu_card_payload_to_human_approval(payload)

    assert approval["thread_id"] == "2c035461-6b47-4b92-a982-7b7eac099c36"
    assert approval["decision"] == "approve"
    assert approval["approver"]["open_id"] == "ou_1"


def test_direct_human_approval_payload_extracts_resume_command_payload() -> None:
    payload = {
        "human_approval": {
            "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
            "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
            "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
            "idempotency_key": "idem-1",
            "decision": "edit",
            "approver": {"source": "internal", "user": "tester"},
            "edited_payload": {"note": "人工修正"},
            "payload_ref": "artifact://proposal/1",
        }
    }

    approval = feishu_card_payload_to_human_approval(payload)

    assert approval["thread_id"] == "2c035461-6b47-4b92-a982-7b7eac099c36"
    assert approval["decision"] == "edit"
    assert approval["approver"] == {"source": "internal", "user": "tester"}
    assert approval["edited_payload"] == {"note": "人工修正"}
    assert approval["payload_ref"] == "artifact://proposal/1"


def test_edit_human_approval_payload_requires_edited_payload() -> None:
    with pytest.raises(ValueError, match="edit requires edited_payload"):
        feishu_card_payload_to_human_approval(
            {
                "human_approval": {
                    "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
                    "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
                    "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
                    "idempotency_key": "idem-1",
                    "decision": "edit",
                    "approver": {"source": "internal", "user": "tester"},
                }
            }
        )

    with pytest.raises(ValueError, match="edit requires edited_payload"):
        feishu_card_payload_to_human_approval(
            {
                "event": {
                    "operator": {"open_id": "ou_1"},
                    "action": {
                        "value": {
                            "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
                            "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
                            "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
                            "idempotency_key": "idem-1",
                            "decision": "edit",
                        }
                    },
                }
            }
        )


def test_langgraph_outbox_handler_resumes_with_langgraph_command() -> None:
    graph = FakeGraph()
    handler = LangGraphOutboxHandler(
        graph=graph,
        use_postgres_checkpointer=False,
        allow_resume_without_interrupt=True,
    )

    handler.resume_graph(
        {
            "event": {
                "operator": {"open_id": "ou_1"},
                "action": {
                    "value": {
                        "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
                        "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
                        "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
                        "idempotency_key": "idem-1",
                        "decision": "reject",
                    }
                },
            }
        }
    )

    command, config = graph.calls[0]
    assert isinstance(command, Command)
    assert command.resume["decision"] == "reject"
    assert config["configurable"]["thread_id"] == "2c035461-6b47-4b92-a982-7b7eac099c36"


def test_langgraph_outbox_handler_resumes_when_runtime_dependencies_are_wired() -> None:
    factory = FakeResumeReadyGraphFactory()
    handler = LangGraphOutboxHandler(
        graph_factory=factory,
        use_postgres_checkpointer=False,
    )

    handler.resume_graph(
        {
            "event": {
                "operator": {"open_id": "ou_1"},
                "action": {
                    "value": {
                        "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
                        "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
                        "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
                        "idempotency_key": "idem-1",
                        "decision": "approve",
                    }
                },
            }
        }
    )

    command, _config = factory.graph.calls[0]
    assert isinstance(command, Command)
    assert command.resume["interrupt_id"] == "ff148eaa-8dbc-4851-83a8-de4a953f738c"


def test_langgraph_outbox_handler_does_not_fake_resume_without_interrupt() -> None:
    handler = LangGraphOutboxHandler(graph=FakeGraph(), use_postgres_checkpointer=False)

    with pytest.raises(RuntimeNotReadyError, match="interrupt"):
        handler.resume_graph(
            {
                "event": {
                    "action": {
                        "value": {
                            "thread_id": "2c035461-6b47-4b92-a982-7b7eac099c36",
                            "action_id": "b99a0bec-7fd4-438c-b56e-314c26a77d8f",
                            "interrupt_id": "ff148eaa-8dbc-4851-83a8-de4a953f738c",
                            "idempotency_key": "idem-1",
                            "decision": "reject",
                        }
                    }
                }
            }
        )
