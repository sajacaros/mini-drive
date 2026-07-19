"""file_favorites + file_recents (즐겨찾기·최근 항목, Phase 8-2/8-3)

Revision ID: 0009_favorites_recents
Revises: 0008_drop_llm_schema
Create Date: 2026-07-19

즐겨찾기(file_favorites)와 최근 항목(file_recents) 테이블을 추가한다. 둘 다 (user_id, file_id)
복합 PK 이고 users/files 삭제 시 CASCADE 로 정리된다.

멱등성(0001~0004 가드 패턴): 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 테이블을
out-of-band 재생성한다(alembic_version 미갱신). 이후 `alembic upgrade head` 가 이미 존재하는
객체를 다시 만들지 않도록 테이블/인덱스별로 존재하면 건너뛴다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_favorites_recents"
down_revision: str | None = "0008_drop_llm_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    insp = sa.inspect(op.get_bind())
    return name in {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    # ── file_favorites (PRD Phase 8-2) ─────────────────────────────────
    if not _has_table("file_favorites"):
        op.create_table(
            "file_favorites",
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "file_id"),
        )

    # ── file_recents (PRD Phase 8-3) ───────────────────────────────────
    if not _has_table("file_recents"):
        op.create_table(
            "file_recents",
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "last_accessed_at", sa.DateTime(timezone=True), nullable=False
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "file_id"),
        )
    if not _has_index("file_recents", "idx_file_recents_last_accessed"):
        op.create_index(
            "idx_file_recents_last_accessed",
            "file_recents",
            ["last_accessed_at"],
        )


def downgrade() -> None:
    if _has_index("file_recents", "idx_file_recents_last_accessed"):
        op.drop_index(
            "idx_file_recents_last_accessed", table_name="file_recents"
        )
    if _has_table("file_recents"):
        op.drop_table("file_recents")
    if _has_table("file_favorites"):
        op.drop_table("file_favorites")
