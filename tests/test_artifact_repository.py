from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.artifacts.repository import ContentAccessDenied, PostgresArtifactStore
from app.schemas.artifacts import AgentArtifact
from app.storage.models import AgentArtifact as AgentArtifactRecord
from app.storage.models import ArtifactBlob, ContentAccessAudit


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self):
        self.records = {}
        self.blobs = {}
        self.statements = []
        self.added = []
        self.flush_count = 0
        self.artifact_by_content_ref = None

    def get(self, model, key):
        if model is AgentArtifactRecord:
            return self.records.get(key)
        if model is ArtifactBlob:
            return self.blobs.get(key)
        raise AssertionError(f"unexpected model: {model}")

    def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.artifact_by_content_ref)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flush_count += 1


def test_artifact_store_read_summary_does_not_read_content_blob() -> None:
    artifact_id = uuid4()
    thread_id = uuid4()
    fake = FakeSession()
    fake.records[artifact_id] = SimpleNamespace(
        id=artifact_id,
        run_id=None,
        thread_id=thread_id,
        kind="CouncilOpinion",
        summary="short summary",
        content_ref="artifact://council/1",
        evidence_refs=[],
        source_refs=[],
        pii_level="none",
        version=1,
        size_tokens_estimate=10,
    )

    result = PostgresArtifactStore(fake).read_summary(
        artifact_id,
        {"allowed_artifact_types_read": ["CouncilOpinion"]},
    )

    assert result.summary == "short summary"
    assert result.content_ref == "artifact://council/1"
    assert fake.statements == []
    assert fake.added == []


def test_artifact_store_blocks_content_read_without_policy_permission() -> None:
    fake = FakeSession()
    fake.artifact_by_content_ref = SimpleNamespace(
        thread_id=uuid4(),
        run_id=None,
        pii_level="none",
        kind="CouncilOpinion",
    )

    with pytest.raises(ContentAccessDenied):
        PostgresArtifactStore(fake).read_content(
            "artifact://blocked",
            {"can_read_artifact_content": False},
            purpose="debug",
        )

    assert fake.added[0].policy_decision["allowed"] is False


def test_artifact_store_blocks_content_read_for_disallowed_artifact_type() -> None:
    fake = FakeSession()
    fake.artifact_by_content_ref = SimpleNamespace(
        thread_id=uuid4(),
        run_id=None,
        pii_level="none",
        kind="CandidateMatchDraft",
    )

    with pytest.raises(ContentAccessDenied, match="CandidateMatchDraft"):
        PostgresArtifactStore(fake).read_content(
            "artifact://candidate",
            {
                "can_read_artifact_content": True,
                "allowed_artifact_types_read": ["CouncilOpinion"],
            },
            purpose="debug",
        )

    assert fake.added[0].policy_decision["allowed"] is False


def test_artifact_store_blocks_content_read_above_pii_policy_level() -> None:
    fake = FakeSession()
    fake.artifact_by_content_ref = SimpleNamespace(
        thread_id=uuid4(),
        run_id=None,
        pii_level="high",
        kind="CandidateMatchDraft",
    )

    with pytest.raises(ContentAccessDenied, match="pii_level high"):
        PostgresArtifactStore(fake).read_content(
            "artifact://candidate",
            {
                "can_read_artifact_content": True,
                "allowed_artifact_types_read": ["CandidateMatchDraft"],
                "pii_access_level": "medium",
            },
            purpose="debug",
        )

    assert fake.added[0].policy_decision["allowed"] is False


def test_artifact_store_content_read_writes_access_audit() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    fake = FakeSession()
    fake.blobs["artifact://full/1"] = SimpleNamespace(
        content_json={"result": "full"},
        content_text=None,
    )
    fake.artifact_by_content_ref = SimpleNamespace(
        thread_id=thread_id,
        run_id=run_id,
        pii_level="low",
        kind="CouncilOpinion",
    )

    result = PostgresArtifactStore(fake).read_content(
        "artifact://full/1",
        {
            "can_read_artifact_content": True,
            "allowed_artifact_types_read": ["CouncilOpinion"],
            "pii_access_level": "low",
            "actor_type": "agent",
            "agent_name": "CouncilSynthesizerAgent",
            "api_key": "secret-value",
        },
        purpose="render debug artifact",
    )

    assert result == {"result": "full"}
    assert len(fake.added) == 1
    audit = fake.added[0]
    assert isinstance(audit, ContentAccessAudit)
    assert audit.content_ref == "artifact://full/1"
    assert audit.content_kind == "artifact"
    assert audit.agent_name == "CouncilSynthesizerAgent"
    assert audit.thread_id == thread_id
    assert audit.run_id == run_id
    assert audit.pii_level == "low"
    assert audit.purpose == "render debug artifact"
    assert audit.policy_decision["api_key"] == "<redacted>"
    assert audit.policy_decision["allowed"] is True


def test_artifact_store_write_uses_summary_ref_model_not_content_field() -> None:
    thread_id = uuid4()
    fake = FakeSession()
    artifact = AgentArtifact(
        thread_id=thread_id,
        producer_agent="StrategyDraftAgent",
        artifact_type="StrategyDraftArtifact",
        summary="strategy summary",
        content_ref="artifact://strategy/1",
    )

    content_ref = PostgresArtifactStore(fake).write(
        artifact,
        {"allowed_artifact_types_write": ["StrategyDraftArtifact"]},
        payload={"full": "content"},
    )

    assert content_ref == "artifact://strategy/1"
    assert len(fake.statements) == 2
    assert fake.flush_count == 1
