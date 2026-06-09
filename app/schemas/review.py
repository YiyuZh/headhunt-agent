from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ReviewGateStatus = Literal["pass", "needs_fix", "needs_human"]
ReviewFindingSeverity = Literal["fix", "human"]


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str
    severity: ReviewFindingSeverity
    message: str
    path: str | None = None


class ReviewGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReviewGateStatus
    artifact_id: UUID | None = None
    artifact_kind: str | None = None
    findings: list[ReviewFinding]
    repair_attempts: int = 0
    reviewed_at: datetime
