"""chat_sessions / chat_messages 테이블 — 대화형 질의의 세션과 이력.

**왜 LangGraph 의 checkpointer 를 쓰지 않는가.** checkpointer 는 에이전트의 *실행 상태*를
위한 것이지 애플리케이션 엔티티를 위한 것이 아니다 — 재개·타임트래블·human-in-the-loop 이
그 목적이고, 스레드 목록·제목·검색 같은 제품 레이어는 LangGraph Platform 의 Threads API 가
따로 얹는다. OSS 만 쓰면 그 레이어는 원래 직접 만든다.

구체적인 대가도 셋이다. `langgraph-checkpoint-postgres` 는 **psycopg 3** 를 쓰는데 이 백엔드는
asyncpg 라 두 번째 드라이버가 들어오고, `setup()` 이 만드는 테이블 넷(`checkpoints`,
`checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`)은 **Alembic 밖**이라
마이그레이션 이력에 안 남으며, 대화 본문이 `checkpoint_blobs.blob BYTEA` 로 직렬화돼 들어가
제목·마지막 메시지 미리보기를 뽑으려면 역직렬화해야 한다(`checkpoints.metadata` 는 JSONB 라
질의되지만 그건 우리가 복제해 넣은 메타데이터일 뿐 본문이 아니다).

그래서 그래프는 checkpointer 없이 매 턴 stateless 로 돌리고, 저장의 진실은 이 두 테이블에만
둔다. 나중에 `interrupt()` 가 필요해지면 이미 쓰고 있는 Redis 에 체크포인터를 붙여 *진행 중*
상태만 맡기면 되고, 그때도 Postgres 가 이중화되지는 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class ChatSession(Base):
    """대화 한 줄기. 사용자당 여러 개이며 목록 화면의 행 하나에 대응한다.

    삭제는 소프트다 — 대화는 사용자가 실수로 지우기 쉬운 자산이고, 하드 삭제하면 메시지까지
    CASCADE 로 함께 사라진다. 목록 질의가 항상 `is_deleted IS FALSE` 를 걸므로 인덱스도
    그 조건을 반영한 부분 인덱스로 둔다.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 첫 질문에서 잘라 만들고 사용자가 고칠 수 있다. 비어 있으면 화면이 "새 대화"로 표시한다.
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"))
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 목록 정렬 키. updated_at 을 쓰지 않는 이유는 제목 변경 같은 메타 수정이 대화를 맨 위로
    # 끌어올리면 안 되기 때문이다 — 목록의 순서는 "마지막으로 대화한 때"여야 한다.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner: Mapped[User] = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index(
            "idx_chat_sessions_user_recent",
            "user_id",
            text("last_message_at DESC NULLS LAST"),
            postgresql_where=text("is_deleted IS FALSE"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<ChatSession id={self.id} user={self.user_id} title={self.title!r}>"


class ChatMessage(Base):
    """대화 안의 메시지 한 건 — 사용자 질문 또는 어시스턴트 답변.

    답변의 **형태**는 `artifact` 에 그대로 담긴다. 모델이 마지막에 부른 렌더 툴
    (`answer_text` · `answer_comparison` …)의 **인자 그 자체**이고, `kind` 필드로 프론트의
    렌더러가 갈린다. 형태를 하나 늘리는 일이 툴 하나 추가로 끝나도록 컬럼을 형태별로 쪼개지
    않았다 — 쪼개면 차트를 붙일 때 마이그레이션이 따라온다.

    `tool_trace` 는 어떤 툴을 몇 번 어떤 인자로 불렀는지다. 답이 이상할 때 원인은 대부분
    "검색이 엉뚱한 걸 가져왔다"이고, 그건 최종 답변만 봐서는 안 보인다.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # 'user' | 'assistant' (models.enums.ChatRole). native enum 미사용(VARCHAR) — 기존 테이블 관례.
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # 사람이 읽는 본문. 사용자 메시지는 질문 원문, 어시스턴트 메시지는 아티팩트의 요약 텍스트다.
    # 목록의 미리보기와 전문 검색이 이 컬럼 하나만 보면 되게 항상 채운다.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    artifact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    tool_trace: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[ChatSession] = relationship("ChatSession", foreign_keys=[session_id])

    __table_args__ = (Index("idx_chat_messages_session", "session_id", "id"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<ChatMessage id={self.id} session={self.session_id} role={self.role}>"
