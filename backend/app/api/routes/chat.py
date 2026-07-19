"""챗봇 라우터 (PRD 6.9, Phase 7-2) — 세션 CRUD + SSE 스트리밍 답변.

세션·메시지는 소유 사용자만 접근한다(타인 404). 질문 전송은 권한 인지 검색(3.7.3) →
LangGraph 파이프라인(3.7.5) 을 거쳐 답변을 **SSE 로 스트리밍**한다. 위키 승격(promote)은
7-3 범위이므로 여기서 만들지 않는다.

라우터는 얇게 유지한다 — 검색/생성은 chat_pipeline, 세션 영속화는 chat 서비스가 담당한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.chat import (
    ChatMessageResponse,
    ChatMessageSendRequest,
    ChatSessionCreateRequest,
    ChatSessionDetailResponse,
    ChatSessionResponse,
)
from app.services import chat as chat_service
from app.services import chat_pipeline
from app.services.chat import ChatServiceError
from app.services.chat_llm import get_chat_provider
from app.services.embeddings import get_embedding_provider

router = APIRouter()

_log = get_logger("app.chat")


def _http_error(exc: ChatServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _sse(event: str, data: object) -> str:
    """SSE 프레임 한 개를 만든다: `event: <name>\\n data: <json>\\n\\n`."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: ChatSessionCreateRequest, user: CurrentUser, session: DbSession
) -> ChatSessionResponse:
    """세션 생성 (빈 title 허용 — 첫 질문으로 자동 설정)."""
    row = await chat_service.create_session(session, user, body.title)
    return ChatSessionResponse.from_session(row)


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_sessions(
    user: CurrentUser, session: DbSession
) -> list[ChatSessionResponse]:
    """내 세션 목록 (최근순)."""
    rows = await chat_service.list_sessions(session, user)
    return [ChatSessionResponse.from_session(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session(
    session_id: int, user: CurrentUser, session: DbSession
) -> ChatSessionDetailResponse:
    """세션 메시지 히스토리 (citations 포함). 소유자만, 타인/부재는 404."""
    try:
        chat_session = await chat_service.get_owned_session(session, user, session_id)
    except ChatServiceError as exc:
        raise _http_error(exc) from exc
    messages = await chat_service.get_messages(session, session_id)
    return ChatSessionDetailResponse(
        id=chat_session.id,
        title=chat_session.title,
        created_at=chat_session.created_at,
        messages=[ChatMessageResponse.from_message(m) for m in messages],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int, user: CurrentUser, session: DbSession
) -> None:
    """세션 삭제 (메시지 CASCADE). 소유자만, 타인/부재는 404."""
    try:
        await chat_service.delete_session(session, user, session_id)
    except ChatServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    body: ChatMessageSendRequest,
    user: CurrentUser,
    session: DbSession,
) -> StreamingResponse:
    """질문 전송 → SSE 스트리밍 답변 (권한 인지 검색 3.7.3 → LangGraph 3.7.5).

    SSE 이벤트: token(델타 반복) → citations(JSON) → done. 완료 시 user/assistant 메시지를
    citations 와 함께 DB 에 저장한다. LLM/임베딩 미구성 시 503 으로 명시적으로 실패한다.
    """
    try:
        chat_session = await chat_service.get_owned_session(session, user, session_id)
    except ChatServiceError as exc:
        raise _http_error(exc) from exc

    # 사용자 대면 기능이라 fail-open 이 아니다 — 프로바이더 미구성이면 명시적 503(PRD 3.7.3).
    embedding_provider = get_embedding_provider(settings)
    if embedding_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="검색 임베딩이 구성되지 않았습니다.",
        )
    chat_provider = get_chat_provider(settings)
    if chat_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM 이 구성되지 않았습니다.",
        )

    question = body.content
    # 이번 질문 이전의 메시지를 히스토리로 사용한다(현재 질문은 포함하지 않음).
    history = await chat_service.get_recent_history(
        session, session_id, settings.chat_history_limit
    )

    async def event_stream() -> AsyncIterator[str]:
        answer = ""
        citations: list[Any] = []
        try:
            async for event in chat_pipeline.stream_answer(
                session=session,
                user=user,
                question=question,
                history=history,
                embedding_provider=embedding_provider,
                chat_provider=chat_provider,
                settings=settings,
            ):
                etype = event.get("type")
                if etype == "token":
                    yield _sse("token", {"value": event["value"]})
                elif etype == "citations":
                    citations = event["value"]
                    yield _sse("citations", citations)
                elif etype == "done":
                    answer = event["answer"]
                    citations = event["citations"]
        except Exception as exc:  # noqa: BLE001 - 생성 실패는 SSE error 로 정중히 전달
            _log.warning("chat_stream_failed", session_id=session_id, error=str(exc))
            yield _sse("error", {"detail": "답변 생성 중 오류가 발생했습니다."})
            return

        # 완료 시 user/assistant 메시지 저장(citations JSONB 포함) + 첫 질문이면 제목 설정.
        await chat_service.maybe_set_title(session, chat_session, question)
        await chat_service.add_message(session, session_id, "user", question)
        assistant = await chat_service.add_message(
            session, session_id, "assistant", answer, citations
        )
        await session.commit()
        await session.refresh(assistant)
        yield _sse(
            "done",
            {
                "session_id": session_id,
                "message_id": assistant.id,
                "title": chat_session.title,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # nginx 버퍼링을 무효화해 토큰이 즉시 전달되게 한다(nginx conf 수정 불요).
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
