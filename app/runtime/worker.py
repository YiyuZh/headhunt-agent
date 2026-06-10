import argparse
import json
import socket
import time
from collections.abc import Iterator
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.feishu.dispatcher import FeishuOutboxDispatcher, OutboxDispatchResult
from app.feishu.gateways import (
    FeishuAuthProvider,
    FeishuHttpBitableGateway,
    FeishuHttpGateway,
)
from app.feishu.outbox_handlers import (
    FeishuOutboxHandler,
    FeishuTaskConfirmationPrepareHandler,
)
from app.runtime.dependencies import build_runtime_graph_factory
from app.runtime.outbox import LangGraphOutboxHandler
from app.storage.database import SessionLocal
from app.storage.repositories import (
    BitableSyncRepository,
    FeishuOutboxRepository,
    FeishuOutboxWriteRepository,
    PayloadRepository,
)


def build_feishu_outbox_dispatcher(
    *,
    session: Session,
    settings: Settings | None = None,
    worker_id: str | None = None,
    use_postgres_checkpointer: bool = True,
) -> FeishuOutboxDispatcher:
    resolved_settings = settings or get_settings()
    auth_provider = FeishuAuthProvider(settings=resolved_settings)
    graph_factory = build_runtime_graph_factory(
        session=session,
        settings=resolved_settings,
    )
    graph_handler = LangGraphOutboxHandler(
        graph_factory=graph_factory,
        use_postgres_checkpointer=use_postgres_checkpointer,
    )
    payload_repository = PayloadRepository(session)
    outbox_writer = FeishuOutboxWriteRepository(session)
    handler = FeishuOutboxHandler(
        payload_repository=payload_repository,
        feishu_gateway=FeishuHttpGateway(auth_provider=auth_provider),
        bitable_gateway=FeishuHttpBitableGateway(auth_provider=auth_provider),
        graph_handler=graph_handler,
        bitable_sync_repository=BitableSyncRepository(session),
        task_confirmation_preparer=FeishuTaskConfirmationPrepareHandler(
            payload_repository=payload_repository,
            outbox_writer=outbox_writer,
            settings=resolved_settings,
        ),
    )
    return FeishuOutboxDispatcher(
        repository=FeishuOutboxRepository(session),
        handler=handler,
        worker_id=worker_id or _default_worker_id(resolved_settings),
    )


def dispatch_once(
    *,
    settings: Settings | None = None,
    worker_id: str | None = None,
) -> OutboxDispatchResult:
    with SessionLocal() as session:
        dispatcher = build_feishu_outbox_dispatcher(
            session=session,
            settings=settings,
            worker_id=worker_id,
        )
        return dispatcher.dispatch_once()


def run_worker_loop(
    *,
    settings: Settings | None = None,
    worker_id: str | None = None,
) -> Iterator[OutboxDispatchResult]:
    resolved_settings = settings or get_settings()
    while True:
        result = dispatch_once(settings=resolved_settings, worker_id=worker_id)
        yield result
        if result.status == "idle":
            time.sleep(resolved_settings.outbox_poll_seconds)


def _default_worker_id(settings: Settings) -> str:
    return settings.outbox_worker_id or f"{socket.gethostname()}:feishu-outbox"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Feishu durable outbox worker.")
    parser.add_argument("--once", action="store_true", help="Dispatch at most one outbox item.")
    args = parser.parse_args()

    if args.once:
        print(json.dumps(asdict(dispatch_once()), ensure_ascii=False))
        return

    for result in run_worker_loop():
        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
