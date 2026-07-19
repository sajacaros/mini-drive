"""wiki_spaces / wiki_sources / wiki_jobs + chat_sessions.space_id (PRD 5.14~5.16, Phase 7-3)

Revision ID: 0006_wiki_spaces_sources_jobs
Revises: 0005_chat_sessions_messages
Create Date: 2026-07-19

Karpathy LLM Wiki 패턴의 스페이스/소스/잡 테이블을 추가한다(PRD 5.14~5.15). 위키 페이지 자체는
별도 테이블이 아니라 `root_folder_id` 하위의 일반 드라이브 파일이다(권한·버전·휴지통 기존 체계
재사용). chat_sessions.space_id 는 0005 주석에 예고된 대로 여기서 추가한다 — FK 대상
wiki_spaces 가 이제 존재하므로(NULL = 전체 접근 범위 대상, ON DELETE SET NULL).

멱등성(0001~0005 가드 패턴): 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 테이블을
out-of-band 재생성한다(alembic_version 미갱신). 그 뒤 backend 기동 시 `alembic upgrade head` 가
이미 존재하는 객체를 다시 만들려다 깨지므로 테이블/컬럼별로 존재하면 건너뛴다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_wiki_spaces_sources_jobs"
down_revision: str | None = "0005_chat_sessions_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # ── wiki_spaces (PRD 5.14) ─────────────────────────────────────────
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
            # 스코프 불변식(PRD 5.14): personal → user_id 만, group → group_id 만.
            sa.CheckConstraint(
                "(scope = 'personal' AND user_id IS NOT NULL AND group_id IS NULL)"
                " OR (scope = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
                name="ck_wiki_spaces_scope",
            ),
        )
        # 내가 접근 가능한 스페이스 조회(개인 소유 / 소속 그룹)용 보조 인덱스.
        op.create_index("idx_wiki_spaces_user", "wiki_spaces", ["user_id"])
        op.create_index("idx_wiki_spaces_group", "wiki_spaces", ["group_id"])

    # ── wiki_sources (PRD 5.14) ────────────────────────────────────────
    if not _has_table("wiki_sources"):
        op.create_table(
            "wiki_sources",
            sa.Column("space_id", sa.BigInteger(), nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "recursive",
                sa.Boolean(),
                server_default=sa.text("true"),
                nullable=False,
            ),
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
            sa.ForeignKeyConstraint(
                ["space_id"], ["wiki_spaces.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("space_id", "file_id"),
        )

    # ── wiki_jobs (PRD 5.15) ───────────────────────────────────────────
    if not _has_table("wiki_jobs"):
        op.create_table(
            "wiki_jobs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("space_id", sa.BigInteger(), nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=True),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                server_default="queued",
                nullable=False,
            ),
            sa.Column(
                "retries", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
            sa.Column("error", sa.Text(), nullable=True),
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
            sa.ForeignKeyConstraint(
                ["space_id"], ["wiki_spaces.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        # 스페이스별 잡 이력(최근순) 조회용.
        op.create_index(
            "idx_wiki_jobs_space", "wiki_jobs", ["space_id", "id"]
        )

    # ── chat_sessions.space_id (PRD 5.16, 0005 에서 예고) ───────────────
    if not _has_column("chat_sessions", "space_id"):
        op.add_column(
            "chat_sessions",
            sa.Column("space_id", sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            "fk_chat_sessions_space",
            "chat_sessions",
            "wiki_spaces",
            ["space_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if _has_column("chat_sessions", "space_id"):
        op.drop_constraint(
            "fk_chat_sessions_space", "chat_sessions", type_="foreignkey"
        )
        op.drop_column("chat_sessions", "space_id")
    if _has_table("wiki_jobs"):
        op.drop_index("idx_wiki_jobs_space", table_name="wiki_jobs")
        op.drop_table("wiki_jobs")
    if _has_table("wiki_sources"):
        op.drop_table("wiki_sources")
    if _has_table("wiki_spaces"):
        op.drop_index("idx_wiki_spaces_group", table_name="wiki_spaces")
        op.drop_index("idx_wiki_spaces_user", table_name="wiki_spaces")
        op.drop_table("wiki_spaces")
