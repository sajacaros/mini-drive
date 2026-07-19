"""위키 v2 — 전사 단일 위키(스페이스 폐기) (wiki-v2 재설계 3장, Phase 7-4)

Revision ID: 0007_wiki_v2_single_wiki
Revises: 0006_wiki_spaces_sources_jobs
Create Date: 2026-07-19

위키 v2 재설계(wiki-v2 D1~D9)로 스페이스 개념을 제거하고 전사 단일 위키로 전환한다:
  - chat_sessions.space_id(FK) → 제거, wiki_scope BOOLEAN NOT NULL DEFAULT false 추가.
  - wiki_sources 재구성 — PK(file_id). space_id·recursive 제거(폴더는 항상 재귀). 기존 행 폐기.
  - wiki_jobs.space_id 제거(기존 행은 유지, space 참조만 소멸).
  - wiki_spaces DROP TABLE.

기존 wiki_sources/wiki_jobs 행은 실패 잡 이력뿐이라 폐기한다(wiki-v2 3장). 다운그레이드는 역방향
재생성(데이터 복원 불요).

멱등성(0001~0006 가드 패턴): 통합 테스트가 dev DB 를 `Base.metadata.create_all` 로 out-of-band
재생성(alembic_version 미갱신)하므로, 기동 시 `alembic upgrade head` 가 이미 최종 상태인 스키마를
다시 변경하려다 깨지지 않도록 테이블/컬럼별로 존재 여부를 확인해 건너뛴다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_wiki_v2_single_wiki"
down_revision: str | None = "0006_wiki_spaces_sources_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes(table)}


def _fk_name(table: str, column: str) -> str | None:
    """table.column 을 참조 컬럼으로 갖는 첫 FK 제약 이름(없으면 None)."""
    if not _has_table(table):
        return None
    insp = sa.inspect(op.get_bind())
    for fk in insp.get_foreign_keys(table):
        if column in fk.get("constrained_columns", []):
            return fk.get("name")
    return None


def upgrade() -> None:
    # ── chat_sessions: space_id 제거 → wiki_scope 추가 ──────────────────
    if _has_column("chat_sessions", "space_id"):
        fk = _fk_name("chat_sessions", "space_id")
        if fk is not None:
            op.drop_constraint(fk, "chat_sessions", type_="foreignkey")
        op.drop_column("chat_sessions", "space_id")
    if not _has_column("chat_sessions", "wiki_scope"):
        op.add_column(
            "chat_sessions",
            sa.Column(
                "wiki_scope",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )

    # ── wiki_jobs: space_id 제거(FK·인덱스 함께) ───────────────────────
    if _has_column("wiki_jobs", "space_id"):
        if _has_index("wiki_jobs", "idx_wiki_jobs_space"):
            op.drop_index("idx_wiki_jobs_space", table_name="wiki_jobs")
        fk = _fk_name("wiki_jobs", "space_id")
        if fk is not None:
            op.drop_constraint(fk, "wiki_jobs", type_="foreignkey")
        op.drop_column("wiki_jobs", "space_id")

    # ── wiki_sources: 재구성(PK file_id). 구 스키마(space_id 보유)면 drop 후 재생성 ──
    if _has_table("wiki_sources") and _has_column("wiki_sources", "space_id"):
        op.drop_table("wiki_sources")
    if not _has_table("wiki_sources"):
        op.create_table(
            "wiki_sources",
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                server_default="queued",
                nullable=False,
            ),
            sa.Column("last_ingested_version", sa.Integer(), nullable=True),
            sa.Column("added_by", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("file_id"),
        )

    # ── wiki_spaces: DROP TABLE(참조 FK 는 위에서 이미 제거됨) ──────────
    if _has_table("wiki_spaces"):
        if _has_index("wiki_spaces", "idx_wiki_spaces_group"):
            op.drop_index("idx_wiki_spaces_group", table_name="wiki_spaces")
        if _has_index("wiki_spaces", "idx_wiki_spaces_user"):
            op.drop_index("idx_wiki_spaces_user", table_name="wiki_spaces")
        op.drop_table("wiki_spaces")


def downgrade() -> None:
    # 역방향 재생성(데이터 복원 불요) — wiki-v2 이전(0006) 구조로 되돌린다.
    if not _has_table("wiki_spaces"):
        op.create_table(
            "wiki_spaces",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("scope", sa.String(length=20), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=True),
            sa.Column("group_id", sa.BigInteger(), nullable=True),
            sa.Column("root_folder_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "settings",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
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
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"]),
            sa.ForeignKeyConstraint(["root_folder_id"], ["files.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "(scope = 'personal' AND user_id IS NOT NULL AND group_id IS NULL)"
                " OR (scope = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
                name="ck_wiki_spaces_scope",
            ),
        )
        op.create_index("idx_wiki_spaces_user", "wiki_spaces", ["user_id"])
        op.create_index("idx_wiki_spaces_group", "wiki_spaces", ["group_id"])

    # wiki_sources: 구 스키마(space_id 복합 PK)로 재구성.
    if _has_table("wiki_sources") and not _has_column("wiki_sources", "space_id"):
        op.drop_table("wiki_sources")
    if not _has_table("wiki_sources"):
        op.create_table(
            "wiki_sources",
            sa.Column("space_id", sa.BigInteger(), nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "recursive", sa.Boolean(), server_default=sa.text("true"), nullable=False
            ),
            sa.Column(
                "status", sa.String(length=20), server_default="queued", nullable=False
            ),
            sa.Column("last_ingested_version", sa.Integer(), nullable=True),
            sa.Column("added_by", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["space_id"], ["wiki_spaces.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("space_id", "file_id"),
        )

    # wiki_jobs: space_id 복원.
    if not _has_column("wiki_jobs", "space_id"):
        op.add_column(
            "wiki_jobs", sa.Column("space_id", sa.BigInteger(), nullable=True)
        )
        op.create_foreign_key(
            "wiki_jobs_space_id_fkey",
            "wiki_jobs",
            "wiki_spaces",
            ["space_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("idx_wiki_jobs_space", "wiki_jobs", ["space_id", "id"])

    # chat_sessions: wiki_scope 제거 → space_id 복원.
    if _has_column("chat_sessions", "wiki_scope"):
        op.drop_column("chat_sessions", "wiki_scope")
    if not _has_column("chat_sessions", "space_id"):
        op.add_column(
            "chat_sessions", sa.Column("space_id", sa.BigInteger(), nullable=True)
        )
        op.create_foreign_key(
            "fk_chat_sessions_space",
            "chat_sessions",
            "wiki_spaces",
            ["space_id"],
            ["id"],
            ondelete="SET NULL",
        )
