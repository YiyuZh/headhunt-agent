from pydantic import SecretStr

from app.core.config import Settings
from app.feishu.dispatcher import FeishuOutboxDispatcher, OutboxDispatchResult
from app.runtime import worker
from app.runtime.outbox import LangGraphOutboxHandler
from app.runtime.worker import build_feishu_outbox_dispatcher


class FakeSession:
    pass


def settings(**overrides) -> Settings:
    data = {
        "llm_provider": "openai_responses",
        "llm_model": "gpt-test",
        "llm_api_key": SecretStr("sk-test"),
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "feishu_app_id": "cli_a",
        "feishu_app_secret": SecretStr("secret"),
        "outbox_worker_id": "worker-test",
    }
    data.update(overrides)
    return Settings(**data)


def test_build_feishu_outbox_dispatcher_wires_real_runtime_dependencies() -> None:
    dispatcher = build_feishu_outbox_dispatcher(
        session=FakeSession(),
        settings=settings(),
        use_postgres_checkpointer=False,
    )

    assert isinstance(dispatcher, FeishuOutboxDispatcher)
    assert dispatcher.worker_id == "worker-test"
    assert isinstance(dispatcher.handler.graph_handler, LangGraphOutboxHandler)
    graph_factory = dispatcher.handler.graph_handler.graph_factory
    assert graph_factory.agent_harness is not None
    assert graph_factory.action_gate is not None
    assert graph_factory.action_executor is not None


def test_build_feishu_outbox_dispatcher_defers_graph_factory_until_needed(monkeypatch) -> None:
    calls = []

    class FakeGraphFactory:
        agent_harness = object()
        action_gate = object()
        action_executor = object()

    def fake_build_runtime_graph_factory(**kwargs):
        calls.append(kwargs)
        return FakeGraphFactory()

    monkeypatch.setattr(worker, "build_runtime_graph_factory", fake_build_runtime_graph_factory)

    dispatcher = build_feishu_outbox_dispatcher(
        session=FakeSession(),
        settings=settings(),
        use_postgres_checkpointer=False,
    )

    assert calls == []
    assert dispatcher.handler.graph_handler.graph_factory.agent_harness is not None
    assert len(calls) == 1


def test_build_feishu_outbox_dispatcher_allows_worker_id_override() -> None:
    dispatcher = build_feishu_outbox_dispatcher(
        session=FakeSession(),
        settings=settings(),
        worker_id="manual-worker",
        use_postgres_checkpointer=False,
    )

    assert dispatcher.worker_id == "manual-worker"


def test_worker_cli_once_prints_dispatch_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        worker,
        "dispatch_once",
        lambda: OutboxDispatchResult(outbox_id=None, kind=None, status="idle"),
    )
    monkeypatch.setattr("sys.argv", ["lietou-outbox-worker", "--once"])

    worker.main()

    assert '"status": "idle"' in capsys.readouterr().out


def test_worker_cli_loop_prints_starting_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["lietou-outbox-worker"])
    monkeypatch.setattr(worker, "get_settings", lambda: settings(outbox_poll_seconds=0.1))
    monkeypatch.setattr(
        worker,
        "run_worker_loop",
        lambda **kwargs: iter([OutboxDispatchResult(outbox_id=None, kind=None, status="idle")]),
    )

    worker.main()

    output = capsys.readouterr().out
    assert '"status": "starting"' in output
    assert '"worker_id": "worker-test"' in output
    assert '"status": "idle"' in output
