"""챗봇 API 요청/응답 스키마 (PRD 6.9, Phase 7-2).

메시지 전송 응답은 SSE 스트림(text/event-stream)이라 Pydantic 응답 모델을 쓰지 않는다 —
세션/히스토리 조회 응답만 여기서 정의한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # 런타임 임포트 순환 회피 — 팩토리 타입 힌트 전용.
    from app.models import ChatMessage, ChatSession


class ChatSessionCreateRequest(BaseModel):
    """세션 생성 (PRD 6.9). title 은 옵션 — 비우면 첫 질문으로 자동 설정된다.

    space_id 를 지정하면 접근 가능한 위키 스페이스여야 하며, 이후 검색이 그 스페이스 범위로
    제한된다(PRD 3.7.5). NULL = 전체 접근 범위 대상.
    """

    title: str = Field(default="", max_length=200)
    space_id: int | None = Field(default=None)


class ChatMessageSendRequest(BaseModel):
    """질문 전송 (PRD 6.9 POST /sessions/{id}/messages). 응답은 SSE 스트림."""

    content: str = Field(min_length=1, max_length=4000)


class ChatMessagePromoteRequest(BaseModel):
    """답변 승격 (PRD 6.9 POST /messages/{id}/promote). 대상 스페이스 + 페이지 제목."""

    space_id: int
    title: str = Field(min_length=1, max_length=100)


class ChatMessagePromoteResponse(BaseModel):
    """승격 결과 — 생성/갱신된 위키 페이지(드라이브 파일) id 와 이름."""

    file_id: int
    name: str


class ChatSessionResponse(BaseModel):
    """세션 요약 (목록/생성 응답)."""

    id: int
    title: str
    space_id: int | None
    created_at: datetime

    @classmethod
    def from_session(cls, s: ChatSession) -> ChatSessionResponse:
        return cls(
            id=s.id, title=s.title, space_id=s.space_id, created_at=s.created_at
        )


class ChatMessageResponse(BaseModel):
    """메시지 한 건 (히스토리 응답). citations 는 [{file_id, chunk_id, version, snippet}]."""

    id: int
    role: str
    content: str
    citations: list[dict[str, Any]] | None
    created_at: datetime

    @classmethod
    def from_message(cls, m: ChatMessage) -> ChatMessageResponse:
        return cls(
            id=m.id,
            role=m.role,
            content=m.content,
            citations=m.citations,
            created_at=m.created_at,
        )


class ChatSessionDetailResponse(BaseModel):
    """세션 + 메시지 히스토리 (PRD 6.9 GET /sessions/{id})."""

    id: int
    title: str
    space_id: int | None
    created_at: datetime
    messages: list[ChatMessageResponse]
