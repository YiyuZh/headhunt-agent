from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import MemoryItem as MemoryItemRecord

RUN_MEMORY_RETENTION_DAYS = 30
LONG_TERM_MEMORY_RETENTION_DAYS = 90
RUN_MEMORY_SCOPE = "RunMemory"
LONG_TERM_MEMORY_SCOPES = {
    "ProjectMemory",
    "AgentMemory",
    "CaseMemory",
    "UserCorrectionMemory",
}
RETENTION_POLICY_30D = "30d"
RETENTION_POLICY_90D = "90d"
RETENTION_POLICY_PERMANENT = "permanent"
PERMANENT_POLICY_ALIASES = {RETENTION_POLICY_PERMANENT, "forever"}
RETENTION_MANAGED_STATUSES = ("active", "pending_review")


@dataclass(frozen=True)
class MemoryRetentionSummary:
    status: str
    scanned_count: int
    default_expiry_count: int
    expired_count: int
    permanent_count: int
    skipped_count: int
    dry_run: bool
    now: datetime


class MemoryRetentionService:
    def __init__(self, session: Session):
        self.session = session

    def run(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> MemoryRetentionSummary:
        effective_now = _aware_datetime(now or datetime.now(UTC))
        rows = (
            self.session.execute(
                select(MemoryItemRecord).where(
                    MemoryItemRecord.status.in_(RETENTION_MANAGED_STATUSES)
                )
            )
            .scalars()
            .all()
        )

        scanned_count = 0
        default_expiry_count = 0
        expired_count = 0
        permanent_count = 0
        skipped_count = 0

        for memory in rows:
            scanned_count += 1
            metadata = _metadata_dict(getattr(memory, "metadata_", None))
            if is_permanent_memory(metadata):
                permanent_count += 1
                if getattr(memory, "expires_at", None) is not None and not dry_run:
                    memory.expires_at = None
                    memory.updated_at = effective_now
                continue

            expires_at = _optional_aware_datetime(getattr(memory, "expires_at", None))
            if expires_at is None:
                expires_at = default_memory_expires_at(
                    scope=str(getattr(memory, "scope", "")),
                    created_at=getattr(memory, "created_at", None),
                    metadata=metadata,
                )
                if expires_at is not None:
                    default_expiry_count += 1
                    if not dry_run:
                        memory.expires_at = expires_at
                        memory.updated_at = effective_now

            if expires_at is None:
                skipped_count += 1
                continue

            if expires_at <= effective_now:
                expired_count += 1
                if not dry_run:
                    memory.status = "expired"
                    memory.updated_at = effective_now

        if not dry_run:
            self.session.flush()

        return MemoryRetentionSummary(
            status="ok",
            scanned_count=scanned_count,
            default_expiry_count=default_expiry_count,
            expired_count=expired_count,
            permanent_count=permanent_count,
            skipped_count=skipped_count,
            dry_run=dry_run,
            now=effective_now,
        )


def default_memory_expires_at(
    *,
    scope: str,
    created_at: datetime | None,
    metadata: Mapping[str, Any] | None = None,
) -> datetime | None:
    policy = retention_policy(metadata)
    if policy in PERMANENT_POLICY_ALIASES:
        return None

    ttl = _retention_window(scope=scope, policy=policy)
    if ttl is None:
        return None
    return _aware_datetime(created_at or datetime.now(UTC)) + ttl


def retention_policy(metadata: Mapping[str, Any] | None) -> str | None:
    if not metadata:
        return None
    raw_value = metadata.get("retention_policy")
    if raw_value is None:
        return None
    value = str(raw_value).strip().lower()
    return value or None


def is_permanent_memory(metadata: Mapping[str, Any] | None) -> bool:
    return retention_policy(metadata) in PERMANENT_POLICY_ALIASES


def _retention_window(*, scope: str, policy: str | None) -> timedelta | None:
    if policy == RETENTION_POLICY_30D:
        return timedelta(days=RUN_MEMORY_RETENTION_DAYS)
    if policy == RETENTION_POLICY_90D:
        return timedelta(days=LONG_TERM_MEMORY_RETENTION_DAYS)
    if scope == RUN_MEMORY_SCOPE:
        return timedelta(days=RUN_MEMORY_RETENTION_DAYS)
    if scope in LONG_TERM_MEMORY_SCOPES:
        return timedelta(days=LONG_TERM_MEMORY_RETENTION_DAYS)
    return None


def _metadata_dict(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _optional_aware_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware_datetime(value)
    return None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
