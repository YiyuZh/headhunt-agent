from datetime import datetime

from pydantic import BaseModel


class MemoryRetentionRunRequest(BaseModel):
    dry_run: bool = False
    now: datetime | None = None


class MemoryRetentionRunResponse(BaseModel):
    status: str
    scanned_count: int
    default_expiry_count: int
    expired_count: int
    permanent_count: int
    skipped_count: int
    dry_run: bool
    now: datetime
