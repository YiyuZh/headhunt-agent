from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from app.storage.models import (
    AgentRun,
    Base,
    FeishuEventLog,
    FeishuOutbox,
    GraphThread,
    MemoryEmbedding,
    MemoryItem,
    UserModelProfile,
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
        "user_model_profiles",
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


def test_user_model_profiles_store_encrypted_byok_metadata() -> None:
    table = UserModelProfile.__table__
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    index_names = {index.name for index in table.indexes}
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    for column in [
        "tenant_id",
        "guild_id",
        "user_id",
        "provider",
        "model_name",
        "display_name",
        "encrypted_api_key",
        "usage",
        "is_default",
        "last_test_status",
    ]:
        assert column in table.columns
    assert "uq_user_model_profile_display_name" in unique_names
    assert "idx_user_model_profiles_owner" in index_names
    assert "uq_user_model_profiles_default_usage" in index_names
    assert "openai" in constraints["ck_user_model_provider"]
    assert "deepseek" in constraints["ck_user_model_provider"]


def test_agent_runs_record_model_profile_metadata() -> None:
    table = AgentRun.__table__
    index_names = {index.name for index in table.indexes}

    assert "model_profile_id" in table.columns
    assert "model_provider" in table.columns
    assert "model_name" in table.columns
    assert "model_owner_user_id" in table.columns
    assert "idx_agent_runs_model_profile" in index_names
    assert "idx_agent_runs_model_owner_started" in index_names
