from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.storage.models import FeishuOutbox
from app.storage.repositories import FeishuOutboxRepository


class OutboxHandler(Protocol):
    def handle(self, item: FeishuOutbox) -> None: ...


class OutboxDispatchError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class OutboxDispatchResult:
    outbox_id: UUID | None
    kind: str | None
    status: str


class FeishuOutboxDispatcher:
    def __init__(
        self,
        *,
        repository: FeishuOutboxRepository,
        handler: OutboxHandler,
        worker_id: str,
        max_attempts: int = 5,
        base_retry_seconds: int = 30,
    ):
        self.repository = repository
        self.handler = handler
        self.worker_id = worker_id
        self.max_attempts = max_attempts
        self.base_retry_seconds = base_retry_seconds

    def dispatch_once(self, *, now: datetime | None = None) -> OutboxDispatchResult:
        current_time = now or datetime.now(UTC)
        try:
            item = self.repository.claim_next(worker_id=self.worker_id, now=current_time)
            if item is not None:
                self.repository.commit()
        except Exception:
            self.repository.rollback()
            raise

        if item is None:
            return OutboxDispatchResult(outbox_id=None, kind=None, status="idle")

        try:
            self.handler.handle(item)
        except OutboxDispatchError as exc:
            self.repository.rollback()
            return self._handle_failure(item, exc, current_time=current_time)
        except Exception as exc:
            self.repository.rollback()
            wrapped = OutboxDispatchError(str(exc))
            return self._handle_failure(item, wrapped, current_time=current_time)

        try:
            self.repository.mark_succeeded(item.id)
            self.repository.commit()
        except Exception:
            self.repository.rollback()
            raise
        return OutboxDispatchResult(outbox_id=item.id, kind=item.kind, status="succeeded")

    def _handle_failure(
        self,
        item: FeishuOutbox,
        exc: OutboxDispatchError,
        *,
        current_time: datetime,
    ) -> OutboxDispatchResult:
        attempts = item.attempt_count or 1
        error = str(exc)
        if attempts >= self.max_attempts:
            try:
                self.repository.mark_dead_letter(outbox_id=item.id, error=error)
                self.repository.commit()
            except Exception:
                self.repository.rollback()
                raise
            return OutboxDispatchResult(
                outbox_id=item.id,
                kind=item.kind,
                status="dead_letter",
            )

        retry_after = exc.retry_after_seconds or self.base_retry_seconds * (2 ** (attempts - 1))
        try:
            self.repository.release_for_retry(
                outbox_id=item.id,
                next_attempt_at=current_time + timedelta(seconds=retry_after),
                error=error,
            )
            self.repository.commit()
        except Exception:
            self.repository.rollback()
            raise
        return OutboxDispatchResult(outbox_id=item.id, kind=item.kind, status="retrying")
