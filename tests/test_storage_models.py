from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from app.storage.models import (
    Base,
    FeishuEventLog,
    FeishuOutbox,
    GraphThread,
    MemoryEmbedding,
    MemoryItem,
)


def test_critical_tables_exist() -> None:
    expected = {
        "feishu_event_logs",
        "feishu_outbox",
        "feishu_card_actions",
        "feishu_bitable_write_chunks",
        "agent_runs",
        "agent_artifacts",
        "artifact_blobs",
        "content_access_audits",
        "memory_items",
        "memory_embeddings",
        "memory_retrieval_audits",
    }

    assert expected.issubset(Base.metadata.tables)


def test_feishu_event_log_has_message_dedupe_constraints() -> None:
    table = FeishuEventLog.__table__
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    index_names = {index.name for index in table.indexes}

    assert "uq_feishu_event_id" in unique_names
    assert "uq_feishu_event_dedupe_key" in unique_names
    assert "uq_feishu_event_idempotency_key" in unique_names
    assert "uq_feishu_event_message_identity" in index_names


def test_outbox_has_lease_index() -> None:
    index_names = {index.name for index in FeishuOutbox.__table__.indexes}

    assert "idx_feishu_outbox_claim_expires" in index_names


def test_graph_thread_supports_queued_status() -> None:
    table = GraphThread.__table__
    status_column = table.columns["status"]
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "'queued'" in str(status_column.server_default.arg)
    assert "queued" in constraints["ck_graph_threads_status"]


def test_memory_items_use_pending_review_status() -> None:
    table = MemoryItem.__table__
    status_column = table.columns["status"]
    index_names = {index.name for index in table.indexes}

    assert "'pending_review'" in str(status_column.server_default.arg)
    for column in [
        "tenant_id",
        "guild_id",
        "user_id",
        "project_id",
        "requisition_id",
        "candidate_id",
        "thread_id",
    ]:
        assert column in table.columns
    assert "idx_memory_tenant_scope_status" in index_names
    assert "idx_memory_guild_scope_status" in index_names
    assert "idx_memory_project_scope_status" in index_names
    assert "idx_memory_requisition_scope_status" in index_names
    assert "idx_memory_candidate_scope_status" in index_names
    assert "idx_memory_thread_scope_status" in index_names


def test_memory_embedding_has_hash_and_vector_index() -> None:
    table = MemoryEmbedding.__table__
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    index_names = {index.name for index in table.indexes}

    assert "content_hash" in table.columns
    assert "model_version" in table.columns
    assert "embedding_version" in table.columns
    assert "uq_memory_embedding_hash" not in unique_names
    assert "idx_memory_embeddings_content_hash" in index_names
    assert any(
        isinstance(index, Index)
        and index.name == "idx_memory_embeddings_vector_hnsw"
        for index in table.indexes
    )
