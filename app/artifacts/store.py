from typing import Protocol
from uuid import UUID

from app.schemas.artifacts import AgentArtifact


class ArtifactStore(Protocol):
    def write(self, artifact: AgentArtifact, policy: dict) -> str: ...
    def read_summary(self, artifact_id: UUID, policy: dict) -> AgentArtifact: ...
    def read_content(self, content_ref: str, policy: dict, purpose: str) -> dict | str: ...

