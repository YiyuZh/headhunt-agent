from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.memory.gateway import PostgresMemoryGateway
from app.memory.vector_store import PostgresVectorMemoryStore, build_memory_search_query
from app.schemas.common import MemoryScope, MemoryStatus, PiiLevel
from app.schemas.memory import MemoryItem, MemoryRef
from app.storage.models import (
    MemoryItem as MemoryItemRecord,
)
from app.storage.models import (
    MemoryRetrievalAudit,
    MemoryUpdateProposal,
)


class FakeEmbeddingGateway:
    def __init__(self):
        self.calls = []

    def embed_texts(self, texts: list[str], purpose: str) -> list[list[float]]:
        self.calls.append((texts, purpose))
        return [[0.01] * 1536 for _ in texts]


class FakeVectorStore:
    def __init__(self, refs: list[MemoryRef]):
        self.refs = refs
        self.calls = []
        self.upserts = []

    def upsert(self, item: MemoryItem, embedding: list[float]) -> str:
        self.upserts.append((item, embedding))
        return f"embedding://{item.memory_id}"

    def search(self, query_embedding: list[float], filters: dict, top_k: int) -> list[MemoryRef]:
        self.calls.append((query_embedding, filters, top_k))
        return self.refs[:top_k]


class FakeSession:
    def __init__(self, records=None):
        self.added = []
        self.statements = []
        self.flush_count = 0
        self.records = records or {}

    def add(self, value):
        self.added.append(value)

    def execute(self, statement):
        self.statements.append(statement)

    def get(self, model, identity):
        return self.records.get((model, identity))

    def flush(self):
        self.flush_count += 1


def make_ref(
    *,
    scope: MemoryScope,
    score: float,
    tokens: int,
    pii: PiiLevel = PiiLevel.none,
    content_ref: str = "memory://1",
) -> MemoryRef:
    return MemoryRef(
        memory_id=uuid4(),
        scope=scope,
        summary=f"{scope.value} summary {score}",
        content_ref=content_ref,
        source_run_id=uuid4(),
        relevance_score=score,
        reason="test hit",
        tokens_estimate=tokens,
        pii_level=pii,
    )


def test_memory_gateway_returns_only_policy_allowed_scopes_and_audits() -> None:
    session = FakeSession()
    embedding = FakeEmbeddingGateway()
    allowed = make_ref(scope=MemoryScope.case, score=0.9, tokens=20)
    disallowed = make_ref(scope=MemoryScope.project, score=1.0, tokens=20)
    store = FakeVectorStore([disallowed, allowed])

    refs = PostgresMemoryGateway(
        session=session,
        embedding_gateway=embedding,
        vector_store=store,
    ).retrieve(
        agent_name="CandidateJudgementAgent",
        task_brief="judge candidate",
        memory_scopes=["CaseMemory", "ProjectMemory"],
        filters={"tenant_id": "tenant-1", "project_id": "project-1"},
        top_k=5,
        max_tokens=200,
        policy={
            "allowed_memory_scopes": ["CaseMemory"],
            "max_memory_items": 5,
            "max_context_tokens": 200,
            "pii_access_level": "medium",
        },
    )

    assert refs == [allowed]
    assert not hasattr(refs[0], "content")
    assert embedding.calls[0][1] == "memory_retrieval:CandidateJudgementAgent"
    assert store.calls[0][1]["allowed_scopes"] == ["CaseMemory"]
    assert store.calls[0][1]["tenant_id"] == "tenant-1"
    assert store.calls[0][1]["project_id"] == "project-1"
    assert store.calls[0][1]["max_pii_level"] == "medium"
    assert "owner_agent" not in store.calls[0][1]
    assert store.calls[0][1]["agent_memory_owner"] == "CandidateJudgementAgent"
    audit = session.added[0]
    assert isinstance(audit, MemoryRetrievalAudit)
    assert audit.allowed_scopes == ["CaseMemory"]
    assert audit.selected_memory_refs[0]["content_ref"] == allowed.content_ref
    assert any("scope_not_allowed" in reason for reason in audit.excluded_reason)


def test_memory_gateway_trims_by_top_k_and_token_budget() -> None:
    first = make_ref(scope=MemoryScope.run, score=0.9, tokens=80, content_ref="memory://a")
    too_big = make_ref(scope=MemoryScope.run, score=0.8, tokens=40, content_ref="memory://b")
    duplicate = make_ref(scope=MemoryScope.run, score=0.7, tokens=10, content_ref="memory://a")
    second = make_ref(scope=MemoryScope.run, score=0.6, tokens=10, content_ref="memory://c")

    refs = PostgresMemoryGateway(
        session=FakeSession(),
        embedding_gateway=FakeEmbeddingGateway(),
        vector_store=FakeVectorStore([first, too_big, duplicate, second]),
    ).retrieve(
        agent_name="IntentRouterAgent",
        task_brief="route",
        memory_scopes=["RunMemory"],
        filters={"thread_id": uuid4()},
        top_k=5,
        max_tokens=100,
        policy={
            "allowed_memory_scopes": ["RunMemory"],
            "max_memory_items": 2,
            "max_context_tokens": 100,
            "pii_access_level": "none",
        },
    )

    assert refs == [first, second]


def test_memory_gateway_blocks_pii_above_policy_level() -> None:
    high_pii = make_ref(
        scope=MemoryScope.case,
        score=0.9,
        tokens=10,
        pii=PiiLevel.high,
        content_ref="memory://high",
    )
    low_pii = make_ref(
        scope=MemoryScope.case,
        score=0.8,
        tokens=10,
        pii=PiiLevel.low,
        content_ref="memory://low",
    )

    refs = PostgresMemoryGateway(
        session=FakeSession(),
        embedding_gateway=FakeEmbeddingGateway(),
        vector_store=FakeVectorStore([high_pii, low_pii]),
    ).retrieve(
        agent_name="CandidateJudgementAgent",
        task_brief="judge",
        memory_scopes=["CaseMemory"],
        filters={"tenant_id": "tenant-1"},
        top_k=5,
        max_tokens=100,
        policy={
            "allowed_memory_scopes": ["CaseMemory"],
            "max_memory_items": 5,
            "max_context_tokens": 100,
            "pii_access_level": "medium",
        },
    )

    assert refs == [low_pii]


def test_memory_gateway_does_not_embed_when_scope_or_budget_is_empty() -> None:
    embedding = FakeEmbeddingGateway()

    refs = PostgresMemoryGateway(
        session=FakeSession(),
        embedding_gateway=embedding,
        vector_store=FakeVectorStore([]),
    ).retrieve(
        agent_name="StrategyDraftAgent",
        task_brief="strategy",
        memory_scopes=["ProjectMemory"],
        filters={},
        top_k=5,
        max_tokens=100,
        policy={
            "allowed_memory_scopes": ["CaseMemory"],
            "max_memory_items": 5,
            "max_context_tokens": 100,
        },
    )

    assert refs == []
    assert embedding.calls == []


def test_memory_gateway_fails_closed_without_scope_filters() -> None:
    embedding = FakeEmbeddingGateway()
    session = FakeSession()

    refs = PostgresMemoryGateway(
        session=session,
        embedding_gateway=embedding,
        vector_store=FakeVectorStore([make_ref(scope=MemoryScope.case, score=0.9, tokens=10)]),
    ).retrieve(
        agent_name="CandidateJudgementAgent",
        task_brief="judge",
        memory_scopes=["CaseMemory"],
        filters={},
        top_k=5,
        max_tokens=100,
        policy={
            "allowed_memory_scopes": ["CaseMemory"],
            "max_memory_items": 5,
            "max_context_tokens": 100,
        },
    )

    assert refs == []
    assert embedding.calls == []
    audit = session.added[0]
    assert "CaseMemory:missing_tenant_or_business_scope_filter" in audit.excluded_reason


def test_memory_gateway_propose_update_vectorizes_run_memory_as_active() -> None:
    session = FakeSession()
    embedding = FakeEmbeddingGateway()
    store = FakeVectorStore([])
    item = MemoryItem(
        scope=MemoryScope.run,
        summary="Run outcome summary",
        content_ref="memory://run/1",
    )
    before = datetime.now(UTC)

    result = PostgresMemoryGateway(
        session=session,
        embedding_gateway=embedding,
        vector_store=store,
    ).propose_update("StrategyDraftAgent", item)

    assert result == str(item.memory_id)
    stored_item, vector = store.upserts[0]
    assert stored_item.status == MemoryStatus.active
    assert stored_item.owner_agent == "StrategyDraftAgent"
    assert stored_item.expires_at is not None
    assert before + timedelta(days=30) <= stored_item.expires_at <= datetime.now(UTC) + timedelta(
        days=30
    )
    assert len(vector) == 1536
    assert session.added == []


def test_memory_gateway_propose_update_keeps_long_term_memory_pending_review() -> None:
    session = FakeSession()
    store = FakeVectorStore([])
    item = MemoryItem(
        scope=MemoryScope.project,
        summary="Project preference",
        content_ref="memory://project/1",
    )
    before = datetime.now(UTC)

    PostgresMemoryGateway(
        session=session,
        embedding_gateway=FakeEmbeddingGateway(),
        vector_store=store,
    ).propose_update("StrategyDraftAgent", item)

    stored_item, _ = store.upserts[0]
    assert stored_item.status == MemoryStatus.pending_review
    assert stored_item.expires_at is not None
    assert before + timedelta(days=90) <= stored_item.expires_at <= datetime.now(UTC) + timedelta(
        days=90
    )
    assert len(session.added) == 1
    assert session.added[0].proposal_type == "create"


def test_memory_gateway_propose_update_clears_permanent_expiry() -> None:
    session = FakeSession()
    store = FakeVectorStore([])
    item = MemoryItem(
        scope=MemoryScope.project,
        summary="Permanent project preference",
        content_ref="memory://project/permanent",
        expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"retention_policy": "permanent"},
    )

    PostgresMemoryGateway(
        session=session,
        embedding_gateway=FakeEmbeddingGateway(),
        vector_store=store,
    ).propose_update("StrategyDraftAgent", item)

    stored_item, _ = store.upserts[0]
    assert stored_item.status == MemoryStatus.pending_review
    assert stored_item.expires_at is None


def test_memory_gateway_approve_update_renews_expired_pending_memory() -> None:
    proposal_id = uuid4()
    memory_id = uuid4()
    proposal = SimpleNamespace(
        id=proposal_id,
        memory_id=memory_id,
        status="pending",
        approver=None,
        decided_at=None,
    )
    memory = SimpleNamespace(
        id=memory_id,
        scope="ProjectMemory",
        status="expired",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime.now(UTC) - timedelta(days=1),
        metadata_={},
    )
    session = FakeSession(
        records={
            (MemoryUpdateProposal, proposal_id): proposal,
            (MemoryItemRecord, memory_id): memory,
        }
    )
    before = datetime.now(UTC)

    PostgresMemoryGateway(
        session=session,
        embedding_gateway=FakeEmbeddingGateway(),
        vector_store=FakeVectorStore([]),
    ).approve_update(str(proposal_id), reviewer="lead")

    assert proposal.status == "approved"
    assert proposal.approver == {"reviewer": "lead"}
    assert memory.status == "active"
    assert memory.expires_at is not None
    assert before + timedelta(days=90) <= memory.expires_at <= datetime.now(UTC) + timedelta(
        days=90
    )
    assert session.flush_count == 1


def test_pgvector_search_query_filters_active_unexpired_memory() -> None:
    statement = build_memory_search_query(
        [0.01] * 1536,
        {
            "allowed_scopes": ["RunMemory", "CaseMemory"],
            "max_pii_level": "medium",
            "now": datetime(2026, 6, 2, tzinfo=UTC),
            "min_confidence": 0.4,
            "agent_memory_owner": "StrategyDraftAgent",
            "tenant_id": "tenant-1",
            "thread_id": uuid4(),
        },
        top_k=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "memory_items.status =" in sql
    assert "memory_items.expires_at IS NULL" in sql
    assert "memory_items.expires_at >" in sql
    assert "memory_items.scope IN" in sql
    assert "memory_items.pii_level IN" in sql
    assert "memory_items.confidence >=" in sql
    assert "memory_items.scope !=" in sql
    assert "memory_items.owner_agent =" in sql
    assert "memory_items.tenant_id =" in sql
    assert "memory_items.thread_id =" in sql
    assert "ORDER BY (memory_embeddings.embedding <=> " in sql


def test_pgvector_search_query_does_not_return_specific_memory_without_boundary() -> None:
    statement = build_memory_search_query(
        [0.01] * 1536,
        {
            "allowed_scopes": ["CaseMemory", "ProjectMemory"],
            "tenant_id": "tenant-1",
        },
        top_k=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "memory_items.tenant_id =" in sql
    assert "memory_items.candidate_id IS NULL" in sql
    assert "memory_items.requisition_id IS NULL" in sql
    assert "memory_items.project_id IS NULL" in sql


def test_pgvector_search_query_allows_general_memory_within_specific_boundary() -> None:
    statement = build_memory_search_query(
        [0.01] * 1536,
        {
            "allowed_scopes": ["CaseMemory", "ProjectMemory"],
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "candidate_id": "candidate-1",
        },
        top_k=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "memory_items.tenant_id =" in sql
    assert "memory_items.tenant_id IS NULL" in sql
    assert "memory_items.project_id =" in sql
    assert "memory_items.project_id IS NULL" in sql
    assert "memory_items.candidate_id =" in sql
    assert "memory_items.candidate_id IS NULL" in sql
    assert "memory_items.requisition_id IS NULL" in sql


def test_pgvector_search_query_excludes_pending_revoked_and_expired_by_construction() -> None:
    statement = build_memory_search_query(
        [0.01] * 1536,
        {"now": datetime.now(UTC) + timedelta(days=1)},
        top_k=3,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "memory_items.status =" in sql
    assert "pending_review" not in sql
    assert "revoked" not in sql


def test_vector_memory_store_upsert_keys_embedding_conflict_by_memory_identity() -> None:
    session = FakeSession()
    item = MemoryItem(
        scope=MemoryScope.run,
        summary="same summary can exist in another memory",
        content_ref="memory://run/unique",
        status=MemoryStatus.active,
    )

    PostgresVectorMemoryStore(
        session,
        model="text-embedding-test",
        model_version="v1",
    ).upsert(item, [0.01] * 1536)

    embedding_insert = session.statements[1]
    sql = str(embedding_insert.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT (memory_id, model, model_version, embedding_version)" in sql
    assert "ON CONFLICT (content_hash, model, model_version)" not in sql
