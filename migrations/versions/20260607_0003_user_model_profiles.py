"""Add Discord BYOK user model profiles.

Revision ID: 20260607_0003
Revises: 20260605_0002
Create Date: 2026-06-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260607_0003"
down_revision = "20260605_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_model_profiles",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("guild_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("usage", sa.Text(), server_default=sa.text("'chat'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "last_test_status",
            sa.Text(),
            server_default=sa.text("'untested'"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('openai','deepseek')", name="ck_user_model_provider"),
        sa.CheckConstraint("usage IN ('chat','embedding')", name="ck_user_model_usage"),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_user_model_status"),
        sa.CheckConstraint(
            "last_test_status IN ('untested','ok','failed')",
            name="ck_user_model_last_test_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "guild_id",
            "user_id",
            "display_name",
            name="uq_user_model_profile_display_name",
        ),
    )
    op.create_index(
        "idx_user_model_profiles_owner",
        "user_model_profiles",
        ["guild_id", "user_id", "status"],
    )
    op.create_index(
        "idx_user_model_profiles_usage",
        "user_model_profiles",
        ["guild_id", "user_id", "usage", "status"],
    )
    op.create_index(
        "uq_user_model_profiles_default_usage",
        "user_model_profiles",
        ["guild_id", "user_id", "usage"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND status = 'active'"),
    )

    op.add_column(
        "agent_runs",
        sa.Column("model_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("agent_runs", sa.Column("model_provider", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("model_name", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("model_owner_user_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_agent_runs_model_profile_id_user_model_profiles",
        "agent_runs",
        "user_model_profiles",
        ["model_profile_id"],
        ["id"],
    )
    op.create_index("idx_agent_runs_model_profile", "agent_runs", ["model_profile_id"])
    op.create_index(
        "idx_agent_runs_model_owner_started",
        "agent_runs",
        ["model_owner_user_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_runs_model_owner_started", table_name="agent_runs")
    op.drop_index("idx_agent_runs_model_profile", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_model_profile_id_user_model_profiles",
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_column("agent_runs", "model_owner_user_id")
    op.drop_column("agent_runs", "model_name")
    op.drop_column("agent_runs", "model_provider")
    op.drop_column("agent_runs", "model_profile_id")

    op.drop_index("uq_user_model_profiles_default_usage", table_name="user_model_profiles")
    op.drop_index("idx_user_model_profiles_usage", table_name="user_model_profiles")
    op.drop_index("idx_user_model_profiles_owner", table_name="user_model_profiles")
    op.drop_table("user_model_profiles")
