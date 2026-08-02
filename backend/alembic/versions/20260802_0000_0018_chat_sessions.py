"""채팅 세션·메시지 — 대화형 질의의 이력 저장

Revision ID: 0018_chat_sessions
Revises: 0017_wiki_single_axis
Create Date: 2026-08-02

  chat_sessions   대화 한 줄기 (사용자별, 소프트 삭제)
  chat_messages   메시지 한 건 (role/content + artifact·citations·tool_trace JSONB)

`artifact` 는 모델이 마지막에 부른 렌더 툴의 **인자 그 자체**이고 `kind` 로 프론트 렌더러가
갈린다. 형태(텍스트·비교표·차트…)마다 컬럼을 쪼개지 않은 이유는 형태를 늘리는 일이 툴 하나
추가로 끝나야 하기 때문이다 — 쪼개면 차트를 붙일 때 마이그레이션이 따라온다.

LangGraph 의 checkpointer 테이블(`checkpoints` 등 넷)은 만들지 않는다. 근거는
`app/models/chat.py` 의 모듈 주석에 있다.

멱등성(0001~0017 가드 패턴): 통합 테스트가 dev DB 를 `Base.metadata.create_all` 로 out-of-band
재생성(alembic_version 미갱신)한다. 기동 시 `alembic upgrade head` 가 이미 있는 객체를 다시
만들려다 깨지지 않도록 테이블별로 존재 여부를 확인해 건너뛴다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_chat_sessions"
down_revision: str | None = "0017_wiki_single_axis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "title",
                sa.String(length=200),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
        # 목록 질의는 항상 "내 것, 안 지운 것, 최근 대화순"이다. 정렬 키가 last_message_at 인
        # 이유는 제목 변경 같은 메타 수정이 대화를 맨 위로 끌어올리면 안 되기 때문이다.
        op.execute(
            sa.text(
                "CREATE INDEX idx_chat_sessions_user_recent ON chat_sessions "
                "(user_id, last_message_at DESC NULLS LAST) WHERE is_deleted IS FALSE"
            )
        )

    if not _has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("session_id", sa.BigInteger(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column(
                "artifact", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column(
                "citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column(
                "tool_trace", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["session_id"], ["chat_sessions.id"], ondelete="CASCADE"
            ),
        )
        # (session_id, id) — 대화를 시간순으로 읽는 것이 유일한 접근 패턴이다.
        op.create_index("idx_chat_messages_session", "chat_messages", ["session_id", "id"])


def downgrade() -> None:
    if _has_table("chat_messages"):
        op.drop_index("idx_chat_messages_session", table_name="chat_messages")
        op.drop_table("chat_messages")
    if _has_table("chat_sessions"):
        op.drop_index("idx_chat_sessions_user_recent", table_name="chat_sessions")
        op.drop_table("chat_sessions")
