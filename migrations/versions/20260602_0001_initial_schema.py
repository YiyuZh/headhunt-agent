"""Initial PostgreSQL and pgvector schema.

Revision ID: 20260602_0001
Revises:
Create Date: 2026-06-02
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "20260602_0001"
down_revision = None
branch_labels = None
depends_on = None


INDEX_TABLES = [
    ("idx_candidates_company_title", "candidates"),
    ("idx_candidates_display_name_trgm", "candidates"),
    ("idx_feishu_event_type_created", "feishu_event_logs"),
    ("idx_feishu_message_id", "feishu_event_logs"),
    ("uq_feishu_event_message_identity", "feishu_event_logs"),
    ("idx_graph_threads_status_updated", "graph_threads"),
    ("idx_requisitions_status_created_at", "requisitions"),
    ("idx_requisitions_title_trgm", "requisitions"),
    ("idx_action_proposals_status_created", "action_proposals"),
    ("idx_action_proposals_thread", "action_proposals"),
    ("idx_agent_runs_agent_started", "agent_runs"),
    ("idx_agent_runs_thread_started", "agent_runs"),
    ("idx_matches_candidate_id", "candidate_requisition_matches"),
    ("idx_matches_decision_status", "candidate_requisition_matches"),
    ("idx_matches_requisition_score", "candidate_requisition_matches"),
    ("idx_card_actions_open_message", "feishu_card_actions"),
    ("idx_card_actions_thread_created", "feishu_card_actions"),
    ("idx_feishu_outbox_claim_expires", "feishu_outbox"),
    ("idx_feishu_outbox_status_next", "feishu_outbox"),
    ("idx_feishu_outbox_thread", "feishu_outbox"),
    ("idx_talent_map_candidate_id", "talent_map_items"),
    ("idx_talent_map_req_company", "talent_map_items"),
    ("idx_talent_map_status", "talent_map_items"),
    ("idx_artifacts_run_id", "agent_artifacts"),
    ("idx_artifacts_thread_kind", "agent_artifacts"),
    ("idx_content_access_agent_created", "content_access_audits"),
    ("idx_content_access_content_created", "content_access_audits"),
    ("idx_content_access_run_id", "content_access_audits"),
    ("idx_content_access_thread_created", "content_access_audits"),
    ("idx_bitable_chunks_outbox_id", "feishu_bitable_write_chunks"),
    ("idx_bitable_chunks_status_created", "feishu_bitable_write_chunks"),
    ("idx_human_approvals_action_id", "human_approvals"),
    ("idx_human_approvals_interrupt", "human_approvals"),
    ("idx_human_approvals_thread", "human_approvals"),
    ("idx_memory_expires_at", "memory_items"),
    ("idx_memory_owner_status", "memory_items"),
    ("idx_memory_source_run_id", "memory_items"),
    ("idx_memory_status_scope", "memory_items"),
    ("idx_memory_audits_agent_created", "memory_retrieval_audits"),
    ("idx_memory_audits_run_id", "memory_retrieval_audits"),
    ("idx_memory_audits_thread", "memory_retrieval_audits"),
    ("idx_memory_embeddings_content_hash", "memory_embeddings"),
    ("idx_memory_embeddings_memory_id", "memory_embeddings"),
    ("idx_memory_embeddings_vector_hnsw", "memory_embeddings"),
    ("idx_memory_proposals_memory_id", "memory_update_proposals"),
    ("idx_memory_proposals_status_created", "memory_update_proposals"),
]

TABLES = [
    "memory_update_proposals",
    "memory_embeddings",
    "memory_retrieval_audits",
    "memory_items",
    "human_approvals",
    "feishu_bitable_write_chunks",
    "content_access_audits",
    "agent_artifacts",
    "talent_map_items",
    "feishu_outbox",
    "feishu_card_actions",
    "candidate_requisition_matches",
    "agent_runs",
    "action_proposals",
    "requisitions",
    "import_runs",
    "graph_threads",
    "feishu_event_logs",
    "feishu_bitable_record_map",
    "candidates",
    "artifact_blobs",
]


def _uuid():
    return postgresql.UUID(as_uuid=True)


def _jsonb():
    return postgresql.JSONB(astext_type=sa.Text())


def _uuid_pk():
    return sa.Column(
        "id",
        _uuid(),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
        primary_key=True,
    )


def _created_at():
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _updated_at():
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _jsonb_default(name: str, default: str):
    return sa.Column(
        name,
        _jsonb(),
        server_default=sa.text(default),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "artifact_blobs",
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("content_json", _jsonb(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("content_ref"),
    )

    op.create_table(
        "candidates",
        _uuid_pk(),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("current_company", sa.Text(), nullable=True),
        sa.Column("current_title", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("profile_summary", sa.Text(), nullable=True),
        sa.Column("profile_content_ref", sa.Text(), nullable=True),
        sa.Column(
            "pii_level",
            sa.Text(),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "pii_level IN ('none','low','medium','high')",
            name="ck_candidates_pii_level",
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')",
            name="ck_candidates_status",
        ),
        sa.UniqueConstraint("external_ref"),
    )

    op.create_table(
        "feishu_bitable_record_map",
        _uuid_pk(),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", _uuid(), nullable=False),
        sa.Column("app_token", sa.Text(), nullable=False),
        sa.Column("table_id", sa.Text(), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("last_sync_status", sa.Text(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "app_token",
            "table_id",
            "record_id",
            name="uq_bitable_record",
        ),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "app_token",
            "table_id",
            name="uq_bitable_entity_table",
        ),
    )

    op.create_table(
        "feishu_event_logs",
        _uuid_pk(),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("tenant_key", sa.Text(), nullable=True),
        sa.Column("app_id", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("payload_ref", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'received'"),
            nullable=False,
        ),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('received','queued','duplicate','failed')",
            name="ck_feishu_event_status",
        ),
        sa.UniqueConstraint("event_id", name="uq_feishu_event_id"),
        sa.UniqueConstraint("dedupe_key", name="uq_feishu_event_dedupe_key"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_feishu_event_idempotency_key",
        ),
    )

    op.create_table(
        "graph_threads",
        _uuid_pk(),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("council_mode", sa.Text(), nullable=True),
        sa.Column("mode_reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        _jsonb_default("state_summary", "'{}'::jsonb"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('queued','running','interrupted','completed','failed')",
            name="ck_graph_threads_status",
        ),
    )

    op.create_table(
        "import_runs",
        _uuid_pk(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column(
            "row_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        _created_at(),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed')",
            name="ck_import_runs_status",
        ),
    )

    op.create_table(
        "requisitions",
        _uuid_pk(),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("seniority", sa.Text(), nullable=True),
        sa.Column("compensation_range", _jsonb(), nullable=True),
        sa.Column("jd_summary", sa.Text(), nullable=False),
        sa.Column("jd_content_ref", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('draft','active','closed')",
            name="ck_requisitions_status",
        ),
        sa.UniqueConstraint("external_ref"),
    )

    op.create_table(
        "action_proposals",
        _uuid_pk(),
        sa.Column("thread_id", _uuid(), nullable=False),
        sa.Column("interrupt_id", _uuid(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("payload_summary", sa.Text(), nullable=False),
        sa.Column("payload_ref", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','executed')",
            name="ck_action_proposals_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "agent_runs",
        _uuid_pk(),
        sa.Column("thread_id", _uuid(), nullable=False),
        sa.Column("node_name", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("council_mode", sa.Text(), nullable=True),
        sa.Column("context_pack_ref", sa.Text(), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        _jsonb_default("memory_refs", "'[]'::jsonb"),
        _jsonb_default("artifact_refs", "'[]'::jsonb"),
        _jsonb_default("source_refs", "'[]'::jsonb"),
        sa.Column("token_estimate", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed')",
            name="ck_agent_runs_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
    )

    op.create_table(
        "candidate_requisition_matches",
        _uuid_pk(),
        sa.Column("candidate_id", _uuid(), nullable=False),
        sa.Column("requisition_id", _uuid(), nullable=False),
        sa.Column("fit_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("fit_summary", sa.Text(), nullable=True),
        _jsonb_default("evidence_refs", "'[]'::jsonb"),
        sa.Column(
            "decision_status",
            sa.Text(),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "decision_status IN "
            "('draft','pending_approval','approved','rejected')",
            name="ck_matches_decision_status",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisitions.id"]),
        sa.UniqueConstraint(
            "candidate_id",
            "requisition_id",
            name="uq_matches_candidate_requisition",
        ),
    )

    op.create_table(
        "feishu_card_actions",
        _uuid_pk(),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("thread_id", _uuid(), nullable=False),
        sa.Column("action_id", _uuid(), nullable=False),
        sa.Column("interrupt_id", _uuid(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("open_message_id", sa.Text(), nullable=True),
        sa.Column("open_chat_id", sa.Text(), nullable=True),
        sa.Column("card_update_token_ref", sa.Text(), nullable=True),
        sa.Column("operator_open_id", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("edited_payload_ref", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'received'"),
            nullable=False,
        ),
        _created_at(),
        sa.CheckConstraint(
            "decision IN ('approve','reject','edit')",
            name="ck_card_actions_decision",
        ),
        sa.CheckConstraint(
            "status IN ('received','queued','resumed','duplicate','failed')",
            name="ck_card_actions_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "feishu_outbox",
        _uuid_pk(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("thread_id", _uuid(), nullable=True),
        sa.Column("payload_ref", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "kind IN "
            "('graph_dispatch','card_send','card_update','bitable_write','resume','task_confirmation_prepare')",
            name="ck_feishu_outbox_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','claimed','succeeded','failed','dead_letter')",
            name="ck_feishu_outbox_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "talent_map_items",
        _uuid_pk(),
        sa.Column("requisition_id", _uuid(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("role_family", sa.Text(), nullable=True),
        sa.Column("candidate_id", _uuid(), nullable=True),
        _jsonb_default("evidence_refs", "'[]'::jsonb"),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('draft','pending_approval','approved','rejected')",
            name="ck_talent_map_status",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisitions.id"]),
    )

    op.create_table(
        "agent_artifacts",
        _uuid_pk(),
        sa.Column("thread_id", _uuid(), nullable=False),
        sa.Column("run_id", _uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=False),
        _jsonb_default("evidence_refs", "'[]'::jsonb"),
        _jsonb_default("source_refs", "'[]'::jsonb"),
        sa.Column(
            "pii_level",
            sa.Text(),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("size_tokens_estimate", sa.Integer(), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "pii_level IN ('none','low','medium','high')",
            name="ck_artifacts_pii_level",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
        sa.UniqueConstraint("content_ref"),
    )

    op.create_table(
        "content_access_audits",
        _uuid_pk(),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("content_kind", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("thread_id", _uuid(), nullable=True),
        sa.Column("run_id", _uuid(), nullable=True),
        sa.Column("pii_level", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("policy_decision", _jsonb(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
    )

    op.create_table(
        "feishu_bitable_write_chunks",
        _uuid_pk(),
        sa.Column("action_id", _uuid(), nullable=True),
        sa.Column("outbox_id", _uuid(), nullable=True),
        sa.Column("app_token", sa.Text(), nullable=False),
        sa.Column("table_id", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("client_token", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        _jsonb_default("record_ids", "'[]'::jsonb"),
        sa.Column("last_error", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "status IN "
            "('pending','succeeded','failed','conflict','dead_letter')",
            name="ck_bitable_chunk_status",
        ),
        sa.ForeignKeyConstraint(["outbox_id"], ["feishu_outbox.id"]),
        sa.UniqueConstraint(
            "action_id",
            "table_id",
            "chunk_index",
            "payload_hash",
            name="uq_bitable_chunk_identity",
        ),
        sa.UniqueConstraint("client_token"),
    )

    op.create_table(
        "human_approvals",
        _uuid_pk(),
        sa.Column("interrupt_id", _uuid(), nullable=False),
        sa.Column("action_id", _uuid(), nullable=False),
        sa.Column("thread_id", _uuid(), nullable=False),
        sa.Column("approver", _jsonb(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("edited_payload_ref", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "decision IN ('approve','reject','edit')",
            name="ck_human_approvals_decision",
        ),
        sa.ForeignKeyConstraint(["action_id"], ["action_proposals.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "memory_items",
        _uuid_pk(),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("owner_agent", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("embedding_ref", sa.Text(), nullable=True),
        sa.Column("source_run_id", _uuid(), nullable=True),
        sa.Column(
            "pii_level",
            sa.Text(),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending_review'"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        _jsonb_default("metadata", "'{}'::jsonb"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "pii_level IN ('none','low','medium','high')",
            name="ck_memory_pii_level",
        ),
        sa.CheckConstraint(
            "scope IN ("
            "'RunMemory','ProjectMemory','AgentMemory',"
            "'CaseMemory','UserCorrectionMemory'"
            ")",
            name="ck_memory_scope",
        ),
        sa.CheckConstraint(
            "status IN ('draft','pending_review','active','revoked','expired')",
            name="ck_memory_status",
        ),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"]),
        sa.UniqueConstraint(
            "content_ref",
            "version",
            name="uq_memory_content_version",
        ),
    )

    op.create_table(
        "memory_retrieval_audits",
        _uuid_pk(),
        sa.Column("thread_id", _uuid(), nullable=True),
        sa.Column("run_id", _uuid(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("task_brief_hash", sa.Text(), nullable=False),
        sa.Column("allowed_scopes", _jsonb(), nullable=False),
        _jsonb_default("filters", "'{}'::jsonb"),
        _jsonb_default("candidate_memory_ids", "'[]'::jsonb"),
        _jsonb_default("selected_memory_refs", "'[]'::jsonb"),
        _jsonb_default("excluded_reason", "'[]'::jsonb"),
        sa.Column("token_estimate", sa.Integer(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["graph_threads.id"]),
    )

    op.create_table(
        "memory_embeddings",
        sa.Column("embedding_ref", sa.Text(), nullable=False),
        sa.Column("memory_id", _uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column(
            "embedding_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_items.id"]),
        sa.PrimaryKeyConstraint("embedding_ref"),
        sa.UniqueConstraint(
            "memory_id",
            "model",
            "model_version",
            "embedding_version",
            name="uq_memory_embedding_version",
        ),
    )

    op.create_table(
        "memory_update_proposals",
        _uuid_pk(),
        sa.Column("memory_id", _uuid(), nullable=False),
        sa.Column("proposal_type", sa.Text(), nullable=False),
        sa.Column("payload_ref", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("approver", _jsonb(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_memory_proposal_status",
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_items.id"]),
    )

    _create_indexes()


def _create_indexes() -> None:
    op.create_index(
        "idx_candidates_company_title",
        "candidates",
        ["current_company", "current_title"],
    )
    op.create_index(
        "idx_candidates_display_name_trgm",
        "candidates",
        ["display_name"],
        postgresql_using="gin",
        postgresql_ops={"display_name": "gin_trgm_ops"},
    )
    op.create_index(
        "idx_feishu_event_type_created",
        "feishu_event_logs",
        ["event_type", "created_at"],
    )
    op.create_index("idx_feishu_message_id", "feishu_event_logs", ["message_id"])
    op.create_index(
        "uq_feishu_event_message_identity",
        "feishu_event_logs",
        ["tenant_key", "event_type", "message_id"],
        unique=True,
        postgresql_where=sa.text("message_id IS NOT NULL"),
    )
    op.create_index(
        "idx_graph_threads_status_updated",
        "graph_threads",
        ["status", "updated_at"],
    )
    op.create_index(
        "idx_requisitions_status_created_at",
        "requisitions",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_requisitions_title_trgm",
        "requisitions",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "idx_action_proposals_status_created",
        "action_proposals",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_action_proposals_thread",
        "action_proposals",
        ["thread_id"],
    )
    op.create_index(
        "idx_agent_runs_agent_started",
        "agent_runs",
        ["agent_name", "started_at"],
    )
    op.create_index(
        "idx_agent_runs_thread_started",
        "agent_runs",
        ["thread_id", "started_at"],
    )
    op.create_index(
        "idx_matches_candidate_id",
        "candidate_requisition_matches",
        ["candidate_id"],
    )
    op.create_index(
        "idx_matches_decision_status",
        "candidate_requisition_matches",
        ["decision_status"],
    )
    op.create_index(
        "idx_matches_requisition_score",
        "candidate_requisition_matches",
        ["requisition_id", "fit_score"],
    )
    op.create_index(
        "idx_card_actions_open_message",
        "feishu_card_actions",
        ["open_message_id"],
    )
    op.create_index(
        "idx_card_actions_thread_created",
        "feishu_card_actions",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "idx_feishu_outbox_claim_expires",
        "feishu_outbox",
        ["status", "claim_expires_at"],
    )
    op.create_index(
        "idx_feishu_outbox_status_next",
        "feishu_outbox",
        ["status", "next_attempt_at"],
    )
    op.create_index("idx_feishu_outbox_thread", "feishu_outbox", ["thread_id"])
    op.create_index(
        "idx_talent_map_candidate_id",
        "talent_map_items",
        ["candidate_id"],
    )
    op.create_index(
        "idx_talent_map_req_company",
        "talent_map_items",
        ["requisition_id", "company_name"],
    )
    op.create_index("idx_talent_map_status", "talent_map_items", ["status"])
    op.create_index("idx_artifacts_run_id", "agent_artifacts", ["run_id"])
    op.create_index(
        "idx_artifacts_thread_kind",
        "agent_artifacts",
        ["thread_id", "kind"],
    )
    op.create_index(
        "idx_content_access_agent_created",
        "content_access_audits",
        ["agent_name", "created_at"],
    )
    op.create_index(
        "idx_content_access_content_created",
        "content_access_audits",
        ["content_ref", "created_at"],
    )
    op.create_index(
        "idx_content_access_run_id",
        "content_access_audits",
        ["run_id"],
    )
    op.create_index(
        "idx_content_access_thread_created",
        "content_access_audits",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "idx_bitable_chunks_outbox_id",
        "feishu_bitable_write_chunks",
        ["outbox_id"],
    )
    op.create_index(
        "idx_bitable_chunks_status_created",
        "feishu_bitable_write_chunks",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_human_approvals_action_id",
        "human_approvals",
        ["action_id"],
    )
    op.create_index(
        "idx_human_approvals_interrupt",
        "human_approvals",
        ["interrupt_id"],
    )
    op.create_index(
        "idx_human_approvals_thread",
        "human_approvals",
        ["thread_id"],
    )
    op.create_index("idx_memory_expires_at", "memory_items", ["expires_at"])
    op.create_index(
        "idx_memory_owner_status",
        "memory_items",
        ["owner_agent", "status"],
    )
    op.create_index(
        "idx_memory_source_run_id",
        "memory_items",
        ["source_run_id"],
    )
    op.create_index(
        "idx_memory_status_scope",
        "memory_items",
        ["status", "scope"],
    )
    op.create_index(
        "idx_memory_audits_agent_created",
        "memory_retrieval_audits",
        ["agent_name", "created_at"],
    )
    op.create_index(
        "idx_memory_audits_run_id",
        "memory_retrieval_audits",
        ["run_id"],
    )
    op.create_index(
        "idx_memory_audits_thread",
        "memory_retrieval_audits",
        ["thread_id"],
    )
    op.create_index(
        "idx_memory_embeddings_content_hash",
        "memory_embeddings",
        ["content_hash", "model", "model_version"],
    )
    op.create_index(
        "idx_memory_embeddings_memory_id",
        "memory_embeddings",
        ["memory_id"],
    )
    op.create_index(
        "idx_memory_embeddings_vector_hnsw",
        "memory_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "idx_memory_proposals_memory_id",
        "memory_update_proposals",
        ["memory_id"],
    )
    op.create_index(
        "idx_memory_proposals_status_created",
        "memory_update_proposals",
        ["status", "created_at"],
    )


def downgrade() -> None:
    for index_name, table_name in reversed(INDEX_TABLES):
        op.drop_index(index_name, table_name=table_name)

    for table_name in TABLES:
        op.drop_table(table_name)
