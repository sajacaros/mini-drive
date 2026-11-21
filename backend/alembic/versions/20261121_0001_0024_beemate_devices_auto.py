"""beemate_devices 에 client_id·즐겨찾기·숨김 — 등록 화면 없이 자동 등록

Revision ID: 0024_beemate_devices_auto
Revises: 0023_beemate
Create Date: 2026-11-21

spec/beemate.md 「기기 — 자동 등록」. 클라이언트가 만든 client_id 로 서버가 기기를 찾거나
만든다. (user_id, client_id) 유일 인덱스가 기존 user_id 인덱스를 겸하므로 그것은 지운다.

0023 으로 이미 만들어진 기기에는 client_id 가 없다. 그 기기를 쓰던 브라우저는 새 client_id 로
다시 붙으며 새 기기가 되므로, 옛 행은 `legacy-{id}` 로 채워 두고 사용자가 지우게 둔다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_beemate_devices_auto"
down_revision: str | None = "0023_beemate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("beemate_devices", sa.Column("client_id", sa.String(64), nullable=True))
    op.execute("UPDATE beemate_devices SET client_id = 'legacy-' || id WHERE client_id IS NULL")
    op.alter_column("beemate_devices", "client_id", nullable=False)
    op.add_column(
        "beemate_devices",
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "beemate_devices",
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_index("idx_beemate_devices_user", table_name="beemate_devices")
    op.create_index(
        "uq_beemate_devices_client",
        "beemate_devices",
        ["user_id", "client_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_beemate_devices_client", table_name="beemate_devices")
    op.create_index("idx_beemate_devices_user", "beemate_devices", ["user_id"])
    op.drop_column("beemate_devices", "is_hidden")
    op.drop_column("beemate_devices", "is_favorite")
    op.drop_column("beemate_devices", "client_id")
