from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PiiLevel


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    kind: str
    summary: str
    content_ref: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    pii_level: PiiLevel = PiiLevel.none
    version: int = 1
    size_tokens_estimate: int = 0


class AgentArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None
    thread_id: UUID
    producer_agent: str
    artifact_type: str
    summary: str
    content_ref: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    pii_level: PiiLevel = PiiLevel.none
    version: int = 1
    size_tokens_estimate: int = 0
