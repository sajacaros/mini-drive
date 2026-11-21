"""beemate_devices / beemate_transfers — 내 기기끼리 파일 보내기

Revision ID: 0023_beemate
Revises: 0022_wiki_reconcile
Create Date: 2026-11-21

spec/beemate.md. 드라이브 파일(`files`)과 분리한 두 테이블이다. 기존 데이터에는 손대지 않는다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023_beemate"
down_revision: str | None = "0022_wiki_reconcile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "beemate_devices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_beemate_devices_user", "beemate_devices", ["user_id"])

    op.create_table(
        "beemate_transfers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_device_id",
            sa.BigInteger(),
            sa.ForeignKey("beemate_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_device_id",
            sa.BigInteger(),
            sa.ForeignKey("beemate_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_beemate_transfers_device",
        "beemate_transfers",
        ["to_device_id", "created_at"],
    )
    op.create_index(
        "idx_beemate_transfers_created", "beemate_transfers", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_beemate_transfers_created", table_name="beemate_transfers")
    op.drop_index("idx_beemate_transfers_device", table_name="beemate_transfers")
    op.drop_table("beemate_transfers")
    op.drop_index("idx_beemate_devices_user", table_name="beemate_devices")
    op.drop_table("beemate_devices")
