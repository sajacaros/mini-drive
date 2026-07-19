"""LLM 기능(위키·챗봇·RAG 인덱싱) 스키마 제거 (Phase 7 전면 제거, 2026-07-19)

Revision ID: 0008_drop_llm_schema
Revises: 0007_wiki_v2_single_wiki
Create Date: 2026-07-19

사용자 결정(파일 공유 코어 집중, LLM 기능은 git 이력으로만 보존)으로 Phase 7 전체를 제거한다.
LLM 관련 테이블·컬럼·확장을 의존 순서에 맞춰 DROP 한다:
  chat_messages → chat_sessions → wiki_jobs → wiki_sources → file_chunks,
  이어서 files.indexing_excluded 컬럼, 마지막으로 vector 확장.

DB 데이터 정리(위키 페이지·시스템 사용자·app_settings 키)는 별도로 이미 처리되었으므로 이
마이그레이션은 스키마만 다룬다.

멱등성(0001~0007 가드 패턴): 통합 테스트가 dev DB 를 `Base.metadata.create_all` 로 out-of-band
재생성(alembic_version 미갱신)한다. LLM 모델은 이제 `Base.metadata` 에서 제거되었으므로 그 경로로
재생성되지 않는다. 기동 시 `alembic upgrade head` 가 이미 없는 객체를 다시 지우려다 깨지지 않도록
테이블/컬럼별로 존재 여부를 확인해 건너뛴다.

downgrade 는 0007(head) 시점 스키마를 best-effort 재생성한다(데이터 복원 불요).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_drop_llm_schema"
down_revision: str | None = "0007_wiki_v2_single_wiki"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 임베딩 차원 — solar-embedding-1-large 기준 4096 (0004 와 동일, downgrade 재생성용).
EMBEDDING_DIM = 4096


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


def upgrade() -> None:
    # ── 의존 순서대로 DROP (자식 → 부모) ───────────────────────────────
    if _has_table("chat_messages"):
        if _has_index("chat_messages", "idx_chat_messages_session"):
            op.drop_index("idx_chat_messages_session", table_name="chat_messages")
        op.drop_table("chat_messages")

    if _has_table("chat_sessions"):
        if _has_index("chat_sessions", "idx_chat_sessions_user"):
            op.drop_index("idx_chat_sessions_user", table_name="chat_sessions")
        op.drop_table("chat_sessions")

    if _has_table("wiki_jobs"):
        if _has_index("wiki_jobs", "idx_wiki_jobs_space"):
            op.drop_index("idx_wiki_jobs_space", table_name="wiki_jobs")
        op.drop_table("wiki_jobs")

    if _has_table("wiki_sources"):
        op.drop_table("wiki_sources")

    if _has_table("file_chunks"):
        if _has_index("file_chunks", "idx_file_chunks_file"):
            op.drop_index("idx_file_chunks_file", table_name="file_chunks")
        op.drop_table("file_chunks")

    # ── files.indexing_excluded 컬럼 ───────────────────────────────────
    if _has_column("files", "indexing_excluded"):
        op.drop_column("files", "indexing_excluded")

    # ── pgvector 확장 ──────────────────────────────────────────────────
    # 더 이상 벡터 컬럼이 없으므로 확장을 제거한다(다른 사용처가 없음 — Phase 7 전용이었다).
    op.execute("DROP EXTENSION IF EXISTS vector")


def downgrade() -> None:
    # 0007(head) 시점 스키마를 best-effort 재생성한다(데이터 복원 불요).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if not _has_column("files", "indexing_excluded"):
        op.add_column(
            "files",
            sa.Column(
                "indexing_excluded",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )

    # ── file_chunks (0004) ─────────────────────────────────────────────
    # embedding(vector) 컬럼은 pgvector 파이썬 패키지 의존을 피하려 raw SQL 로 추가한다.
    if not _has_table("file_chunks"):
        op.create_table(
            "file_chunks",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("token_count", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "file_id",
                "version",
                "chunk_index",
                name="uq_file_chunks_file_version_index",
            ),
        )
        op.execute(
            f"ALTER TABLE file_chunks ADD COLUMN embedding vector({EMBEDDING_DIM})"
        )
        op.create_index("idx_file_chunks_file", "file_chunks", ["file_id"])

    # ── chat_sessions (0005 + 0007 wiki_scope) ─────────────────────────
    if not _has_table("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "title", sa.String(length=200), server_default="", nullable=False
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "wiki_scope",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_chat_sessions_user", "chat_sessions", ["user_id", "created_at"]
        )

    # ── chat_messages (0005) ───────────────────────────────────────────
    if not _has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.BigInteger(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column(
                "citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["session_id"], ["chat_sessions.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_chat_messages_session", "chat_messages", ["session_id", "id"]
        )

    # ── wiki_sources (0007 재구성 스키마 — PK file_id) ─────────────────
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

    # ── wiki_jobs (0006 생성 - 0007 에서 space_id 제거된 스키마) ────────
    if not _has_table("wiki_jobs"):
        op.create_table(
            "wiki_jobs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
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
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
