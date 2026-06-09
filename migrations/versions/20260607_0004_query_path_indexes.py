"""Add query-path indexes for inspection and approval lookups.

Revision ID: 20260607_0004
Revises: 20260607_0003
Create Date: 2026-06-07
"""

from alembic import op

revision = "20260607_0004"
down_revision = "20260607_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_artifacts_thread_created",
        "agent_artifacts",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "idx_action_proposals_interrupt_created",
        "action_proposals",
        ["interrupt_id", "created_at"],
    )
    op.create_index(
        "idx_action_proposals_thread_status_created",
        "action_proposals",
        ["thread_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_action_proposals_thread_status_created",
        table_name="action_proposals",
    )
    op.drop_index(
        "idx_action_proposals_interrupt_created",
        table_name="action_proposals",
    )
    op.drop_index("idx_artifacts_thread_created", table_name="agent_artifacts")
