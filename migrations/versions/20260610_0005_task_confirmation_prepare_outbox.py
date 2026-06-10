"""Allow async task confirmation prepare outbox kind.

Revision ID: 20260610_0005
Revises: 20260607_0004
Create Date: 2026-06-10 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260610_0005"
down_revision: str | None = "20260607_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_KIND_CONSTRAINT = (
    "kind IN "
    "('graph_dispatch','card_send','card_update','bitable_write','resume',"
    "'task_confirmation_prepare')"
)
OLD_KIND_CONSTRAINT = (
    "kind IN ('graph_dispatch','card_send','card_update','bitable_write','resume')"
)


def upgrade() -> None:
    op.drop_constraint("ck_feishu_outbox_kind", "feishu_outbox", type_="check")
    op.create_check_constraint(
        "ck_feishu_outbox_kind",
        "feishu_outbox",
        NEW_KIND_CONSTRAINT,
    )


def downgrade() -> None:
    op.drop_constraint("ck_feishu_outbox_kind", "feishu_outbox", type_="check")
    op.create_check_constraint(
        "ck_feishu_outbox_kind",
        "feishu_outbox",
        OLD_KIND_CONSTRAINT,
    )
