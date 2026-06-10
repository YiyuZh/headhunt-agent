from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - dependency is installed in Phase 1 env
    Vector = None

EMBEDDING_DIMENSION = 1536


class Base(DeclarativeBase):
    pass


def uuid_pk():
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CreatedAtMixin:
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Requisition(TimestampMixin, Base):
    __tablename__ = "requisitions"

    id = uuid_pk()
    external_ref = mapped_column(Text, unique=True)
    title = mapped_column(Text, nullable=False)
    department = mapped_column(Text)
    location = mapped_column(Text)
    seniority = mapped_column(Text)
    compensation_range = mapped_column(JSONB)
    jd_summary = mapped_column(Text, nullable=False)
    jd_content_ref = mapped_column(Text)
    status = mapped_column(Text, nullable=False, server_default=text("'draft'"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','closed')",
            name="ck_requisitions_status",
        ),
        Index("idx_requisitions_status_created_at", "status", "created_at"),
        Index(
            "idx_requisitions_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )


class Candidate(TimestampMixin, Base):
    __tablename__ = "candidates"

    id = uuid_pk()
    external_ref = mapped_column(Text, unique=True)
    display_name = mapped_column(Text, nullable=False)
    current_company = mapped_column(Text)
    current_title = mapped_column(Text)
    location = mapped_column(Text)
    profile_summary = mapped_column(Text)
    profile_content_ref = mapped_column(Text)
    pii_level = mapped_column(Text, nullable=False, server_default=text("'none'"))
    status = mapped_column(Text, nullable=False, server_default=text("'active'"))

    __table_args__ = (
        CheckConstraint(
            "pii_level IN ('none','low','medium','high')",
            name="ck_candidates_pii_level",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_candidates_status"),
        Index("idx_candidates_company_title", "current_company", "current_title"),
        Index(
            "idx_candidates_display_name_trgm",
            "display_name",
            postgresql_using="gin",
            postgresql_ops={"display_name": "gin_trgm_ops"},
        ),
    )


class CandidateRequisitionMatch(TimestampMixin, Base):
    __tablename__ = "candidate_requisition_matches"

    id = uuid_pk()
    candidate_id = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False)
    requisition_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requisitions.id"),
        nullable=False,
    )
    fit_score = mapped_column(Numeric(5, 4))
    fit_summary = mapped_column(Text)
    evidence_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    decision_status = mapped_column(Text, nullable=False, server_default=text("'draft'"))

    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "requisition_id",
            name="uq_matches_candidate_requisition",
        ),
        CheckConstraint(
            "decision_status IN ('draft','pending_approval','approved','rejected')",
            name="ck_matches_decision_status",
        ),
        Index("idx_matches_candidate_id", "candidate_id"),
        Index("idx_matches_requisition_score", "requisition_id", "fit_score"),
        Index("idx_matches_decision_status", "decision_status"),
    )


class TalentMapItem(TimestampMixin, Base):
    __tablename__ = "talent_map_items"

    id = uuid_pk()
    requisition_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requisitions.id"),
        nullable=False,
    )
    company_name = mapped_column(Text, nullable=False)
    role_family = mapped_column(Text)
    candidate_id = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    evidence_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    confidence = mapped_column(Numeric(5, 4))
    status = mapped_column(Text, nullable=False, server_default=text("'draft'"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','pending_approval','approved','rejected')",
            name="ck_talent_map_status",
        ),
        Index("idx_talent_map_req_company", "requisition_id", "company_name"),
        Index("idx_talent_map_candidate_id", "candidate_id"),
        Index("idx_talent_map_status", "status"),
    )


class GraphThread(TimestampMixin, Base):
    __tablename__ = "graph_threads"

    id = uuid_pk()
    source = mapped_column(Text, nullable=False)
    source_ref = mapped_column(Text)
    task_type = mapped_column(Text, nullable=False)
    council_mode = mapped_column(Text)
    mode_reason = mapped_column(Text)
    status = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    state_summary = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','interrupted','completed','failed')",
            name="ck_graph_threads_status",
        ),
        Index("idx_graph_threads_status_updated", "status", "updated_at"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = uuid_pk()
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"), nullable=False)
    node_name = mapped_column(Text, nullable=False)
    agent_name = mapped_column(Text, nullable=False)
    council_mode = mapped_column(Text)
    context_pack_ref = mapped_column(Text, nullable=False)
    input_summary = mapped_column(Text)
    output_summary = mapped_column(Text)
    memory_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    artifact_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    model_profile_id = mapped_column(UUID(as_uuid=True), ForeignKey("user_model_profiles.id"))
    model_provider = mapped_column(Text)
    model_name = mapped_column(Text)
    model_owner_user_id = mapped_column(Text)
    token_estimate = mapped_column(Integer)
    status = mapped_column(Text, nullable=False, server_default=text("'running'"))
    error = mapped_column(Text)
    started_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','succeeded','failed')",
            name="ck_agent_runs_status",
        ),
        Index("idx_agent_runs_thread_started", "thread_id", "started_at"),
        Index("idx_agent_runs_agent_started", "agent_name", "started_at"),
        Index("idx_agent_runs_model_profile", "model_profile_id"),
        Index("idx_agent_runs_model_owner_started", "model_owner_user_id", "started_at"),
    )


class UserModelProfile(TimestampMixin, Base):
    __tablename__ = "user_model_profiles"

    id = uuid_pk()
    tenant_id = mapped_column(Text)
    guild_id = mapped_column(Text, nullable=False)
    user_id = mapped_column(Text, nullable=False)
    provider = mapped_column(Text, nullable=False)
    model_name = mapped_column(Text, nullable=False)
    display_name = mapped_column(Text, nullable=False)
    encrypted_api_key = mapped_column(Text)
    base_url = mapped_column(Text)
    usage = mapped_column(Text, nullable=False, server_default=text("'chat'"))
    status = mapped_column(Text, nullable=False, server_default=text("'active'"))
    is_default = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_test_status = mapped_column(Text, nullable=False, server_default=text("'untested'"))
    last_used_at = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "guild_id",
            "user_id",
            "display_name",
            name="uq_user_model_profile_display_name",
        ),
        CheckConstraint("provider IN ('openai','deepseek')", name="ck_user_model_provider"),
        CheckConstraint("usage IN ('chat','embedding')", name="ck_user_model_usage"),
        CheckConstraint("status IN ('active','revoked')", name="ck_user_model_status"),
        CheckConstraint(
            "last_test_status IN ('untested','ok','failed')",
            name="ck_user_model_last_test_status",
        ),
        Index("idx_user_model_profiles_owner", "guild_id", "user_id", "status"),
        Index("idx_user_model_profiles_usage", "guild_id", "user_id", "usage", "status"),
        Index(
            "uq_user_model_profiles_default_usage",
            "guild_id",
            "user_id",
            "usage",
            unique=True,
            postgresql_where=text("is_default = true AND status = 'active'"),
        ),
    )


class AgentArtifact(CreatedAtMixin, Base):
    __tablename__ = "agent_artifacts"

    id = uuid_pk()
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"), nullable=False)
    run_id = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    kind = mapped_column(Text, nullable=False)
    summary = mapped_column(Text, nullable=False)
    content_ref = mapped_column(Text, nullable=False, unique=True)
    evidence_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    pii_level = mapped_column(Text, nullable=False, server_default=text("'none'"))
    version = mapped_column(Integer, nullable=False, server_default=text("1"))
    size_tokens_estimate = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "pii_level IN ('none','low','medium','high')",
            name="ck_artifacts_pii_level",
        ),
        Index("idx_artifacts_thread_kind", "thread_id", "kind"),
        Index("idx_artifacts_thread_created", "thread_id", "created_at"),
        Index("idx_artifacts_run_id", "run_id"),
    )


class ArtifactBlob(CreatedAtMixin, Base):
    __tablename__ = "artifact_blobs"

    content_ref = mapped_column(Text, primary_key=True)
    media_type = mapped_column(Text, nullable=False)
    content_json = mapped_column(JSONB)
    content_text = mapped_column(Text)
    sha256 = mapped_column(Text, nullable=False)


class ContentAccessAudit(CreatedAtMixin, Base):
    __tablename__ = "content_access_audits"

    id = uuid_pk()
    content_ref = mapped_column(Text, nullable=False)
    content_kind = mapped_column(Text, nullable=False)
    actor_type = mapped_column(Text, nullable=False)
    actor_id = mapped_column(Text)
    agent_name = mapped_column(Text)
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"))
    run_id = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    pii_level = mapped_column(Text, nullable=False)
    purpose = mapped_column(Text, nullable=False)
    policy_decision = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_content_access_content_created", "content_ref", "created_at"),
        Index("idx_content_access_thread_created", "thread_id", "created_at"),
        Index("idx_content_access_run_id", "run_id"),
        Index("idx_content_access_agent_created", "agent_name", "created_at"),
    )


class MemoryItem(TimestampMixin, Base):
    __tablename__ = "memory_items"

    id = uuid_pk()
    scope = mapped_column(Text, nullable=False)
    owner_agent = mapped_column(Text)
    summary = mapped_column(Text, nullable=False)
    content_ref = mapped_column(Text, nullable=False)
    embedding_ref = mapped_column(Text)
    source_run_id = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    tenant_id = mapped_column(Text)
    guild_id = mapped_column(Text)
    user_id = mapped_column(Text)
    project_id = mapped_column(Text)
    requisition_id = mapped_column(Text)
    candidate_id = mapped_column(Text)
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"))
    pii_level = mapped_column(Text, nullable=False, server_default=text("'none'"))
    status = mapped_column(Text, nullable=False, server_default=text("'pending_review'"))
    confidence = mapped_column(Numeric(5, 4))
    version = mapped_column(Integer, nullable=False, server_default=text("1"))
    expires_at = mapped_column(DateTime(timezone=True))
    metadata_ = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("content_ref", "version", name="uq_memory_content_version"),
        CheckConstraint(
            "scope IN ("
            "'RunMemory','ProjectMemory','AgentMemory',"
            "'CaseMemory','UserCorrectionMemory'"
            ")",
            name="ck_memory_scope",
        ),
        CheckConstraint(
            "pii_level IN ('none','low','medium','high')",
            name="ck_memory_pii_level",
        ),
        CheckConstraint(
            "status IN ('draft','pending_review','active','revoked','expired')",
            name="ck_memory_status",
        ),
        Index("idx_memory_status_scope", "status", "scope"),
        Index("idx_memory_owner_status", "owner_agent", "status"),
        Index("idx_memory_source_run_id", "source_run_id"),
        Index("idx_memory_tenant_scope_status", "tenant_id", "scope", "status"),
        Index("idx_memory_guild_scope_status", "guild_id", "scope", "status"),
        Index("idx_memory_project_scope_status", "project_id", "scope", "status"),
        Index("idx_memory_requisition_scope_status", "requisition_id", "scope", "status"),
        Index("idx_memory_candidate_scope_status", "candidate_id", "scope", "status"),
        Index("idx_memory_thread_scope_status", "thread_id", "scope", "status"),
        Index("idx_memory_expires_at", "expires_at"),
    )


class MemoryEmbedding(CreatedAtMixin, Base):
    __tablename__ = "memory_embeddings"

    embedding_ref = mapped_column(Text, primary_key=True)
    memory_id = mapped_column(UUID(as_uuid=True), ForeignKey("memory_items.id"), nullable=False)
    model = mapped_column(Text, nullable=False)
    model_version = mapped_column(Text, nullable=False)
    embedding_version = mapped_column(Integer, nullable=False, server_default=text("1"))
    dimension = mapped_column(Integer, nullable=False)
    content_hash = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIMENSION) if Vector else Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "model",
            "model_version",
            "embedding_version",
            name="uq_memory_embedding_version",
        ),
        Index("idx_memory_embeddings_memory_id", "memory_id"),
        Index("idx_memory_embeddings_content_hash", "content_hash", "model", "model_version"),
        Index(
            "idx_memory_embeddings_vector_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MemoryRetrievalAudit(CreatedAtMixin, Base):
    __tablename__ = "memory_retrieval_audits"

    id = uuid_pk()
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"))
    run_id = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    agent_name = mapped_column(Text, nullable=False)
    task_brief_hash = mapped_column(Text, nullable=False)
    allowed_scopes = mapped_column(JSONB, nullable=False)
    filters = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    candidate_memory_ids = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    selected_memory_refs = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    excluded_reason = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    token_estimate = mapped_column(Integer)

    __table_args__ = (
        Index("idx_memory_audits_agent_created", "agent_name", "created_at"),
        Index("idx_memory_audits_thread", "thread_id"),
        Index("idx_memory_audits_run_id", "run_id"),
    )


class MemoryUpdateProposal(CreatedAtMixin, Base):
    __tablename__ = "memory_update_proposals"

    id = uuid_pk()
    memory_id = mapped_column(UUID(as_uuid=True), ForeignKey("memory_items.id"), nullable=False)
    proposal_type = mapped_column(Text, nullable=False)
    payload_ref = mapped_column(Text, nullable=False)
    status = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    approver = mapped_column(JSONB)
    decision_reason = mapped_column(Text)
    decided_at = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_memory_proposal_status",
        ),
        Index("idx_memory_proposals_memory_id", "memory_id"),
        Index("idx_memory_proposals_status_created", "status", "created_at"),
    )


class FeishuEventLog(CreatedAtMixin, Base):
    __tablename__ = "feishu_event_logs"

    id = uuid_pk()
    event_id = mapped_column(Text, nullable=False)
    event_type = mapped_column(Text, nullable=False)
    tenant_key = mapped_column(Text)
    app_id = mapped_column(Text)
    message_id = mapped_column(Text)
    dedupe_key = mapped_column(Text, nullable=False)
    idempotency_key = mapped_column(Text, nullable=False)
    payload_hash = mapped_column(Text, nullable=False)
    payload_ref = mapped_column(Text, nullable=False)
    status = mapped_column(Text, nullable=False, server_default=text("'received'"))

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_feishu_event_id"),
        UniqueConstraint("dedupe_key", name="uq_feishu_event_dedupe_key"),
        UniqueConstraint("idempotency_key", name="uq_feishu_event_idempotency_key"),
        CheckConstraint(
            "status IN ('received','queued','duplicate','failed')",
            name="ck_feishu_event_status",
        ),
        Index("idx_feishu_event_type_created", "event_type", "created_at"),
        Index("idx_feishu_message_id", "message_id"),
        Index(
            "uq_feishu_event_message_identity",
            "tenant_key",
            "event_type",
            "message_id",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
    )


class FeishuOutbox(TimestampMixin, Base):
    __tablename__ = "feishu_outbox"

    id = uuid_pk()
    kind = mapped_column(Text, nullable=False)
    idempotency_key = mapped_column(Text, nullable=False, unique=True)
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"))
    payload_ref = mapped_column(Text, nullable=False)
    status = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claimed_by = mapped_column(Text)
    claimed_at = mapped_column(DateTime(timezone=True))
    claim_expires_at = mapped_column(DateTime(timezone=True))
    last_error = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "kind IN ("
            "'graph_dispatch','card_send','card_update','bitable_write','resume',"
            "'task_confirmation_prepare'"
            ")",
            name="ck_feishu_outbox_kind",
        ),
        CheckConstraint(
            "status IN ('pending','claimed','succeeded','failed','dead_letter')",
            name="ck_feishu_outbox_status",
        ),
        Index("idx_feishu_outbox_status_next", "status", "next_attempt_at"),
        Index("idx_feishu_outbox_claim_expires", "status", "claim_expires_at"),
        Index("idx_feishu_outbox_thread", "thread_id"),
    )


class FeishuCardAction(CreatedAtMixin, Base):
    __tablename__ = "feishu_card_actions"

    id = uuid_pk()
    event_id = mapped_column(Text, nullable=False, unique=True)
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"), nullable=False)
    action_id = mapped_column(UUID(as_uuid=True), nullable=False)
    interrupt_id = mapped_column(UUID(as_uuid=True))
    idempotency_key = mapped_column(Text, nullable=False, unique=True)
    open_message_id = mapped_column(Text)
    open_chat_id = mapped_column(Text)
    card_update_token_ref = mapped_column(Text)
    operator_open_id = mapped_column(Text)
    decision = mapped_column(Text, nullable=False)
    edited_payload_ref = mapped_column(Text)
    status = mapped_column(Text, nullable=False, server_default=text("'received'"))

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve','reject','edit')",
            name="ck_card_actions_decision",
        ),
        CheckConstraint(
            "status IN ('received','queued','resumed','duplicate','failed')",
            name="ck_card_actions_status",
        ),
        Index("idx_card_actions_open_message", "open_message_id"),
        Index("idx_card_actions_thread_created", "thread_id", "created_at"),
    )


class FeishuBitableRecordMap(Base):
    __tablename__ = "feishu_bitable_record_map"

    id = uuid_pk()
    entity_type = mapped_column(Text, nullable=False)
    entity_id = mapped_column(UUID(as_uuid=True), nullable=False)
    app_token = mapped_column(Text, nullable=False)
    table_id = mapped_column(Text, nullable=False)
    record_id = mapped_column(Text, nullable=False)
    last_sync_status = mapped_column(Text, nullable=False)
    last_sync_at = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "app_token",
            "table_id",
            name="uq_bitable_entity_table",
        ),
        UniqueConstraint(
            "app_token",
            "table_id",
            "record_id",
            name="uq_bitable_record",
        ),
    )


class FeishuBitableWriteChunk(TimestampMixin, Base):
    __tablename__ = "feishu_bitable_write_chunks"

    id = uuid_pk()
    action_id = mapped_column(UUID(as_uuid=True))
    outbox_id = mapped_column(UUID(as_uuid=True), ForeignKey("feishu_outbox.id"))
    app_token = mapped_column(Text, nullable=False)
    table_id = mapped_column(Text, nullable=False)
    chunk_index = mapped_column(Integer, nullable=False)
    payload_hash = mapped_column(Text, nullable=False)
    client_token = mapped_column(Text, nullable=False, unique=True)
    status = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    record_ids = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    last_error = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "action_id",
            "table_id",
            "chunk_index",
            "payload_hash",
            name="uq_bitable_chunk_identity",
        ),
        CheckConstraint(
            "status IN ('pending','succeeded','failed','conflict','dead_letter')",
            name="ck_bitable_chunk_status",
        ),
        Index("idx_bitable_chunks_outbox_id", "outbox_id"),
        Index("idx_bitable_chunks_status_created", "status", "created_at"),
    )


class ActionProposal(TimestampMixin, Base):
    __tablename__ = "action_proposals"

    id = uuid_pk()
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"), nullable=False)
    interrupt_id = mapped_column(UUID(as_uuid=True), nullable=False)
    action_type = mapped_column(Text, nullable=False)
    payload_summary = mapped_column(Text, nullable=False)
    payload_ref = mapped_column(Text, nullable=False)
    idempotency_key = mapped_column(Text, nullable=False, unique=True)
    status = mapped_column(Text, nullable=False, server_default=text("'pending'"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','executed')",
            name="ck_action_proposals_status",
        ),
        Index("idx_action_proposals_status_created", "status", "created_at"),
        Index("idx_action_proposals_thread", "thread_id"),
        Index("idx_action_proposals_interrupt_created", "interrupt_id", "created_at"),
        Index("idx_action_proposals_thread_status_created", "thread_id", "status", "created_at"),
    )


class HumanApproval(CreatedAtMixin, Base):
    __tablename__ = "human_approvals"

    id = uuid_pk()
    interrupt_id = mapped_column(UUID(as_uuid=True), nullable=False)
    action_id = mapped_column(UUID(as_uuid=True), ForeignKey("action_proposals.id"), nullable=False)
    thread_id = mapped_column(UUID(as_uuid=True), ForeignKey("graph_threads.id"), nullable=False)
    approver = mapped_column(JSONB, nullable=False)
    decision = mapped_column(Text, nullable=False)
    edited_payload_ref = mapped_column(Text)
    idempotency_key = mapped_column(Text, nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve','reject','edit')",
            name="ck_human_approvals_decision",
        ),
        Index("idx_human_approvals_interrupt", "interrupt_id"),
        Index("idx_human_approvals_action_id", "action_id"),
        Index("idx_human_approvals_thread", "thread_id"),
    )


class ImportRun(Base):
    __tablename__ = "import_runs"

    id = uuid_pk()
    kind = mapped_column(Text, nullable=False)
    source = mapped_column(Text, nullable=False)
    source_ref = mapped_column(Text)
    status = mapped_column(Text, nullable=False, server_default=text("'running'"))
    row_count = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_summary = mapped_column(Text)
    created_at = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','succeeded','failed')",
            name="ck_import_runs_status",
        ),
    )
