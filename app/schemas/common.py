from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class CouncilMode(StrEnum):
    triage = "triage"
    lite = "lite"
    standard = "standard"
    full_council = "full_council"


class PiiLevel(StrEnum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class MemoryScope(StrEnum):
    run = "RunMemory"
    project = "ProjectMemory"
    agent = "AgentMemory"
    case = "CaseMemory"
    user_correction = "UserCorrectionMemory"


class MemoryStatus(StrEnum):
    draft = "draft"
    pending_review = "pending_review"
    active = "active"
    revoked = "revoked"
    expired = "expired"


class IdModel(BaseModel):
    id: UUID = Field(description="Stable UUID identifier")

