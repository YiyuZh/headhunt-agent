import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.schemas.memory import MemoryItem as MemoryItemSchema
from app.schemas.memory import MemoryRef
from app.storage.models import MemoryEmbedding
from app.storage.models import MemoryItem as MemoryItemRecord

PII_ORDER = ("none", "low", "medium", "high")
LONG_TERM_SCOPE_FILTER_FIELDS = (
    "tenant_id",
    "guild_id",
    "user_id",
    "project_id",
    "requisition_id",
    "candidate_id",
)


class PostgresVectorMemoryStore:
    def __init__(
        self,
        session: Session,
        *,
        model: str = "unknown",
        model_version: str = "unknown",
    ):
        self.session = session
        self.model = model
        self.model_version = model_version

    def upsert(
        self,
        item: MemoryItemSchema,
        embedding: list[float],
        *,
        content_hash: str | None = None,
        embedding_ref: str | None = None,
    ) -> str:
        resolved_embedding_ref = (
            embedding_ref
            or item.embedding_ref
            or _default_embedding_ref(item, self.model, self.model_version)
        )
        resolved_content_hash = content_hash or _memory_content_hash(item)
        memory_result = self.session.execute(
            pg_insert(MemoryItemRecord)
            .values(
                id=item.memory_id,
                scope=item.scope.value,
                owner_agent=item.owner_agent,
                summary=item.summary,
                content_ref=item.content_ref,
                embedding_ref=resolved_embedding_ref,
                source_run_id=item.source_run_id,
                tenant_id=item.tenant_id,
                guild_id=item.guild_id,
                user_id=item.user_id,
                project_id=item.project_id,
                requisition_id=item.requisition_id,
                candidate_id=item.candidate_id,
                thread_id=item.thread_id,
                pii_level=item.pii_level.value,
                status=item.status.value,
                confidence=item.confidence,
                version=item.version,
                expires_at=item.expires_at,
                metadata_=item.metadata,
            )
            .on_conflict_do_update(
                index_elements=["content_ref", "version"],
                set_={
                    "summary": item.summary,
                    "embedding_ref": resolved_embedding_ref,
                    "pii_level": item.pii_level.value,
                    "status": item.status.value,
                    "confidence": item.confidence,
                    "expires_at": item.expires_at,
                    "tenant_id": item.tenant_id,
                    "guild_id": item.guild_id,
                    "user_id": item.user_id,
                    "project_id": item.project_id,
                    "requisition_id": item.requisition_id,
                    "candidate_id": item.candidate_id,
                    "thread_id": item.thread_id,
                    "metadata": item.metadata,
                },
            )
            .returning(MemoryItemRecord.id)
        )
        persisted_memory_id = _returned_memory_id(memory_result, item.memory_id)
        self.session.execute(
            pg_insert(MemoryEmbedding)
            .values(
                embedding_ref=resolved_embedding_ref,
                memory_id=persisted_memory_id,
                model=self.model,
                model_version=self.model_version,
                embedding_version=1,
                dimension=len(embedding),
                content_hash=resolved_content_hash,
                embedding=embedding,
            )
            .on_conflict_do_update(
                index_elements=["memory_id", "model", "model_version", "embedding_version"],
                set_={
                    "content_hash": resolved_content_hash,
                    "embedding": embedding,
                    "dimension": len(embedding),
                },
            )
        )
        self.session.flush()
        return resolved_embedding_ref

    def search(
        self,
        query_embedding: list[float],
        filters: dict[str, Any],
        top_k: int,
    ) -> list[MemoryRef]:
        statement = build_memory_search_query(query_embedding, filters, top_k)
        rows = self.session.execute(statement).all()
        refs: list[MemoryRef] = []
        for memory, relevance_score in rows:
            score = _clamp_score(relevance_score)
            refs.append(
                MemoryRef(
                    memory_id=memory.id,
                    scope=memory.scope,
                    summary=memory.summary,
                    content_ref=memory.content_ref,
                    source_run_id=memory.source_run_id,
                    tenant_id=memory.tenant_id,
                    guild_id=memory.guild_id,
                    user_id=memory.user_id,
                    project_id=memory.project_id,
                    requisition_id=memory.requisition_id,
                    candidate_id=memory.candidate_id,
                    thread_id=memory.thread_id,
                    relevance_score=score,
                    reason=f"pgvector similarity hit; score={score:.3f}",
                    tokens_estimate=_estimate_tokens(memory.summary),
                    pii_level=memory.pii_level,
                )
            )
        return refs


def build_memory_search_query(
    query_embedding: list[float],
    filters: dict[str, Any],
    top_k: int,
):
    now = filters.get("now") or datetime.now(UTC)
    distance = MemoryEmbedding.embedding.cosine_distance(query_embedding)
    relevance_score = (literal(1.0) - distance).label("relevance_score")

    statement = (
        select(MemoryItemRecord, relevance_score)
        .join(MemoryEmbedding, MemoryEmbedding.memory_id == MemoryItemRecord.id)
        .where(MemoryItemRecord.status == "active")
        .where(or_(MemoryItemRecord.expires_at.is_(None), MemoryItemRecord.expires_at > now))
        .order_by(distance.asc())
        .limit(top_k)
    )

    allowed_scopes = filters.get("allowed_scopes") or filters.get("scopes")
    if allowed_scopes:
        statement = statement.where(
            MemoryItemRecord.scope.in_([str(scope) for scope in allowed_scopes])
        )

    max_pii_level = filters.get("max_pii_level") or filters.get("pii_access_level")
    if max_pii_level:
        statement = statement.where(
            MemoryItemRecord.pii_level.in_(_allowed_pii_levels(max_pii_level))
        )

    owner_agent = filters.get("owner_agent")
    if owner_agent:
        statement = statement.where(
            or_(MemoryItemRecord.owner_agent.is_(None), MemoryItemRecord.owner_agent == owner_agent)
        )

    agent_memory_owner = filters.get("agent_memory_owner")
    if agent_memory_owner:
        statement = statement.where(
            or_(
                MemoryItemRecord.scope != "AgentMemory",
                MemoryItemRecord.owner_agent.is_(None),
                MemoryItemRecord.owner_agent == agent_memory_owner,
            )
        )

    min_confidence = filters.get("min_confidence")
    if min_confidence is not None:
        statement = statement.where(MemoryItemRecord.confidence >= min_confidence)

    metadata_contains = filters.get("metadata_contains")
    if isinstance(metadata_contains, dict) and metadata_contains:
        statement = statement.where(MemoryItemRecord.metadata_.contains(metadata_contains))

    statement = _apply_scope_boundary_filters(statement, filters)

    return statement


def _apply_scope_boundary_filters(statement, filters: dict[str, Any]):
    for field_name in LONG_TERM_SCOPE_FILTER_FIELDS:
        column = getattr(MemoryItemRecord, field_name)
        value = filters.get(field_name)
        if value is not None:
            statement = statement.where(or_(column == value, column.is_(None)))
        else:
            statement = statement.where(column.is_(None))

    thread_id = filters.get("thread_id")
    if thread_id is not None:
        statement = statement.where(
            or_(
                MemoryItemRecord.thread_id == thread_id,
                and_(
                    MemoryItemRecord.scope != "RunMemory",
                    MemoryItemRecord.thread_id.is_(None),
                ),
            )
        )
    else:
        statement = statement.where(MemoryItemRecord.thread_id.is_(None))
    return statement


def _allowed_pii_levels(max_pii_level: str) -> list[str]:
    if max_pii_level not in PII_ORDER:
        return ["none"]
    return list(PII_ORDER[: PII_ORDER.index(max_pii_level) + 1])


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _memory_content_hash(item: MemoryItemSchema) -> str:
    identity = f"{item.content_ref}\n{item.summary}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _default_embedding_ref(item: MemoryItemSchema, model: str, model_version: str) -> str:
    identity = f"{item.content_ref}:{item.version}:{model}:{model_version}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"memory-embedding://{digest}/1"


def _returned_memory_id(result: Any, fallback):
    if hasattr(result, "scalar_one"):
        return result.scalar_one()
    if hasattr(result, "scalar"):
        value = result.scalar()
        if value is not None:
            return value
    return fallback
