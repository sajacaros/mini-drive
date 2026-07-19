"""chat_sessions / chat_messages (권한 인지 챗봇, PRD 5.16, Phase 7-2)

Revision ID: 0005_chat_sessions_messages
Revises: 0004_file_chunks_pgvector
Create Date: 2026-07-19

챗봇 대화 세션·메시지 테이블을 추가한다. citations 는 검색 통과 청크에서 구조적으로 산출한
[{file_id, chunk_id, version, snippet}] JSONB 다.

space_id(wiki_spaces FK)는 **여기서 만들지 않는다** — FK 대상 wiki_spaces 가 7-3 에서
생기므로 컬럼·FK 모두 7-3 마이그레이션에서 chat_sessions 에 함께 추가한다.

멱등성(0001~0004 가드 패턴): 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 테이블을
out-of-band 재생성한다(alembic_version 미갱신). 그 뒤 backend 기동 시 `alembic upgrade head` 가
이미 존재하는 객체를 다시 만들려다 깨지므로 테이블별로 존재하면 건너뛴다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_chat_sessions_messages"
down_revision: str | None = "0004_file_chunks_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # ── chat_sessions (PRD 5.16) ───────────────────────────────────────
    # space_id 는 7-3 에서 추가(wiki_spaces FK). 여기서는 만들지 않는다.
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
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_chat_sessions_user", "chat_sessions", ["user_id", "created_at"]
        )

    # ── chat_messages (PRD 5.16) ───────────────────────────────────────
    if not _has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.BigInteger(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        # 세션 메시지 히스토리(시간순) 조회용 보조 인덱스.
        op.create_index(
            "idx_chat_messages_session", "chat_messages", ["session_id", "id"]
        )


def downgrade() -> None:
    if _has_table("chat_messages"):
        op.drop_index("idx_chat_messages_session", table_name="chat_messages")
        op.drop_table("chat_messages")
    if _has_table("chat_sessions"):
        op.drop_index("idx_chat_sessions_user", table_name="chat_sessions")
        op.drop_table("chat_sessions")
