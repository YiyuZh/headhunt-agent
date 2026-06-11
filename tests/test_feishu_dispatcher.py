from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.feishu.dispatcher import FeishuOutboxDispatcher, OutboxDispatchError


@dataclass
class FakeOutboxItem:
    id: UUID
    kind: str
    attempt_count: int


class FakeOutboxRepository:
    def __init__(self, item: FakeOutboxItem | None):
        self.item = item
        self.succeeded = None
        self.retry = None
        self.dead_letter = None
        self.commits = 0
        self.rollbacks = 0

    def claim_next(self, *, worker_id: str, lease_seconds: int, now: datetime):
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.now = now
        return self.item

    def mark_succeeded(self, outbox_id: UUID) -> None:
        self.succeeded = outbox_id

    def release_for_retry(
        self,
        *,
        outbox_id: UUID,
        next_attempt_at: datetime,
        error: str,
    ) -> None:
        self.retry = {
            "outbox_id": outbox_id,
            "next_attempt_at": next_attempt_at,
            "error": error,
        }

    def mark_dead_letter(self, *, outbox_id: UUID, error: str) -> None:
        self.dead_letter = {"outbox_id": outbox_id, "error": error}

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class SuccessHandler:
    def __init__(self):
        self.items = []

    def handle(self, item) -> None:
        self.items.append(item)


class FailingHandler:
    def __init__(self, exc: Exception):
        self.exc = exc

    def handle(self, item) -> None:
        raise self.exc


def test_dispatcher_marks_success() -> None:
    item = FakeOutboxItem(id=uuid4(), kind="graph_dispatch", attempt_count=0)
    repository = FakeOutboxRepository(item)
    handler = SuccessHandler()

    result = FeishuOutboxDispatcher(
        repository=repository,
        handler=handler,
        worker_id="worker-1",
    ).dispatch_once(now=datetime(2026, 6, 2, tzinfo=UTC))

    assert result.status == "succeeded"
    assert repository.succeeded == item.id
    assert repository.lease_seconds == 300
    assert repository.commits == 2
    assert repository.rollbacks == 0
    assert handler.items == [item]


def test_dispatcher_uses_retry_after_on_failure() -> None:
    item = FakeOutboxItem(id=uuid4(), kind="card_update", attempt_count=1)
    repository = FakeOutboxRepository(item)

    result = FeishuOutboxDispatcher(
        repository=repository,
        handler=FailingHandler(OutboxDispatchError("rate limited", retry_after_seconds=90)),
        worker_id="worker-1",
    ).dispatch_once(now=datetime(2026, 6, 2, tzinfo=UTC))

    assert result.status == "retrying"
    assert repository.retry["outbox_id"] == item.id
    assert repository.retry["next_attempt_at"].second == 30
    assert repository.retry["error"] == "rate limited"
    assert repository.dead_letter is None
    assert repository.commits == 2


def test_dispatcher_allows_custom_claim_lease_seconds() -> None:
    item = FakeOutboxItem(id=uuid4(), kind="task_confirmation_prepare", attempt_count=0)
    repository = FakeOutboxRepository(item)

    FeishuOutboxDispatcher(
        repository=repository,
        handler=SuccessHandler(),
        worker_id="worker-1",
        claim_lease_seconds=600,
    ).dispatch_once(now=datetime(2026, 6, 2, tzinfo=UTC))

    assert repository.lease_seconds == 600


def test_dispatcher_marks_dead_letter_after_max_attempts() -> None:
    item = FakeOutboxItem(id=uuid4(), kind="bitable_write", attempt_count=5)
    repository = FakeOutboxRepository(item)

    result = FeishuOutboxDispatcher(
        repository=repository,
        handler=FailingHandler(OutboxDispatchError("still failing")),
        worker_id="worker-1",
        max_attempts=5,
    ).dispatch_once(now=datetime(2026, 6, 2, tzinfo=UTC))

    assert result.status == "dead_letter"
    assert repository.dead_letter == {"outbox_id": item.id, "error": "still failing"}
    assert repository.retry is None
    assert repository.commits == 2


def test_dispatcher_does_not_dead_letter_before_max_attempts() -> None:
    item = FakeOutboxItem(id=uuid4(), kind="bitable_write", attempt_count=4)
    repository = FakeOutboxRepository(item)

    result = FeishuOutboxDispatcher(
        repository=repository,
        handler=FailingHandler(OutboxDispatchError("try again")),
        worker_id="worker-1",
        max_attempts=5,
    ).dispatch_once(now=datetime(2026, 6, 2, tzinfo=UTC))

    assert result.status == "retrying"
    assert repository.retry["outbox_id"] == item.id
    assert repository.dead_letter is None
