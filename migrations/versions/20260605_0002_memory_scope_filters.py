"""Add MemoryGateway scope filter columns.

Revision ID: 20260605_0002
Revises: 20260602_0001
Create Date: 2026-06-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260605_0002"
down_revision = "20260602_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_items", sa.Column("tenant_id", sa.Text(), nullable=True))
    op.add_column("memory_items", sa.Column("guild_id", sa.Text(), nullable=True))
    op.add_column("memory_items", sa.Column("user_id", sa.Text(), nullable=True))
    op.add_column("memory_items", sa.Column("project_id", sa.Text(), nullable=True))
    op.add_column("memory_items", sa.Column("requisition_id", sa.Text(), nullable=True))
    op.add_column("memory_items", sa.Column("candidate_id", sa.Text(), nullable=True))
    op.add_column(
        "memory_items",
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_memory_items_thread_id_graph_threads",
        "memory_items",
        "graph_threads",
        ["thread_id"],
        ["id"],
    )
    op.create_index(
        "idx_memory_tenant_scope_status",
        "memory_items",
        ["tenant_id", "scope", "status"],
    )
    op.create_index(
        "idx_memory_guild_scope_status",
        "memory_items",
        ["guild_id", "scope", "status"],
    )
    op.create_index(
        "idx_memory_project_scope_status",
        "memory_items",
        ["project_id", "scope", "status"],
    )
    op.create_index(
        "idx_memory_requisition_scope_status",
        "memory_items",
        ["requisition_id", "scope", "status"],
    )
    op.create_index(
        "idx_memory_candidate_scope_status",
        "memory_items",
        ["candidate_id", "scope", "status"],
    )
    op.create_index(
        "idx_memory_thread_scope_status",
        "memory_items",
        ["thread_id", "scope", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_memory_thread_scope_status", table_name="memory_items")
    op.drop_index("idx_memory_candidate_scope_status", table_name="memory_items")
    op.drop_index("idx_memory_requisition_scope_status", table_name="memory_items")
    op.drop_index("idx_memory_project_scope_status", table_name="memory_items")
    op.drop_index("idx_memory_guild_scope_status", table_name="memory_items")
    op.drop_index("idx_memory_tenant_scope_status", table_name="memory_items")
    op.drop_constraint(
        "fk_memory_items_thread_id_graph_threads",
        "memory_items",
        type_="foreignkey",
    )
    op.drop_column("memory_items", "thread_id")
    op.drop_column("memory_items", "candidate_id")
    op.drop_column("memory_items", "requisition_id")
    op.drop_column("memory_items", "project_id")
    op.drop_column("memory_items", "user_id")
    op.drop_column("memory_items", "guild_id")
    op.drop_column("memory_items", "tenant_id")
