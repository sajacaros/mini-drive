"""chat_sessions / chat_messages 테이블 (PRD 5.16, wiki-v2 4.5, Phase 7-4).

권한 인지 챗봇의 대화 세션과 메시지를 담는다. 세션·메시지는 소유 사용자만 조회 가능하며
(공유 불가), citations 의 파일 링크는 클릭 시점에 다시 ensure_file_access 를 통과한다 —
대화 이후 권한이 회수된 파일은 열리지 않는다.

설계 (PRD 5.16, wiki-v2 4.5):
  - `wiki_scope`(BOOLEAN): true 면 검색 후보를 전사 위키 범위(wiki_file_scope — 위키 페이지 ∪
    공유 소스)로 제한하고 위키 페이지를 우선 배치한다. 위키 v2 재설계로 스페이스(space_id)를
    폐기하고 이 불리언 토글로 대체했다(wiki-v2 D-전면개정).
  - `citations` 는 검색 통과 청크에서 구조적으로 산출한 [{file_id, chunk_id, version, snippet}]
    JSONB. 모델 출력 파싱이 아니라 파이프라인이 직접 채운다(위조 불가).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


class ChatSession(Base):
    """챗 대화 세션 (PRD 5.16). 소유 사용자만 접근 가능(공유 불가)."""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 전사 위키 범위 토글(wiki-v2 4.5). true 면 검색을 위키 범위로 제한하고 위키 페이지를
    # 우선 배치한다. 스페이스 폐기로 space_id FK 를 이 불리언으로 대체했다.
    wiki_scope: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )

    __table_args__ = (
        # 내 세션 목록(최근순) 조회용.
        Index("idx_chat_sessions_user", "user_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<ChatSession id={self.id} user={self.user_id} title={self.title!r}>"


class ChatMessage(Base):
    """챗 메시지 한 건 (PRD 5.16). role=user/assistant, citations 는 검색 통과 청크에서 산출."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user / assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # [{file_id, chunk_id, version, snippet}] — 검색 통과 청크에서 구조적으로 산출(PRD 3.7.5).
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    __table_args__ = (
        # 세션 메시지 히스토리(시간순) 조회용.
        Index("idx_chat_messages_session", "session_id", "id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<ChatMessage id={self.id} session={self.session_id} role={self.role}>"
