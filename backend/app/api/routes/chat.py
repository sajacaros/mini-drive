"""대화형 질의 라우터 (/api/chat).

기존 `POST /api/wiki/ask` 는 그대로 둔다 — 세션 없이 한 번 묻고 마는 경로에는 여전히 쓸모가
있고, 기존 화면과 통합 테스트가 걸려 있다.

오류 처리는 `routes/wiki.py` 의 규칙을 따른다: 모델 서버의 **주소·포트·상태 코드**가 담긴
예외 문자열을 `detail` 에 그대로 실으면 브라우저까지 내려가 내부 엔드포인트가 공개된다.
진단에 필요한 쪽은 로그이고, 사용자에게 필요한 것은 "지금 내가 뭘 하면 되는가"다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.models import ChatMessage, ChatSession
from app.schemas.chat import (
    ChatAskRequest,
    ChatAskResponse,
    ChatCitation,
    ChatMessageItem,
    ChatSessionCreateRequest,
    ChatSessionDetail,
    ChatSessionItem,
    ChatSessionList,
    ChatSessionRenameRequest,
    ChatToolCall,
)
from app.services.chat import agent, sessions
from app.services.chat.sessions import ChatSessionError
from app.services.llm import LLMError, LLMRequestError
from app.services.storage import get_storage

router = APIRouter()
log = get_logger(__name__)


def _http_error(exc: ChatSessionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _require_enabled() -> None:
    if not settings.chat_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대화 기능이 꺼져 있습니다.",
        )


def _session_item(row: ChatSession) -> ChatSessionItem:
    return ChatSessionItem(
        id=row.id,
        title=row.title,
        created_at=row.created_at.isoformat(),
        last_message_at=(
            row.last_message_at.isoformat() if row.last_message_at else None
        ),
    )


def _message_item(row: ChatMessage) -> ChatMessageItem:
    return ChatMessageItem(
        id=row.id,
        role=row.role,
        content=row.content,
        artifact=row.artifact,
        citations=[ChatCitation(**c) for c in row.citations or []],
        tool_trace=[ChatToolCall(**t) for t in row.tool_trace or []],
        created_at=row.created_at.isoformat(),
    )


@router.post("/sessions", response_model=ChatSessionItem, status_code=201)
async def create_session(
    payload: ChatSessionCreateRequest, user: CurrentUser, session: DbSession
) -> ChatSessionItem:
    """새 대화를 연다. 제목은 첫 질문에서 자동으로 만들어진다."""
    _require_enabled()
    row = await sessions.create(session, user.id, title=payload.title)
    return _session_item(row)


@router.get("/sessions", response_model=ChatSessionList)
async def list_sessions(
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ChatSessionList:
    """내 대화 목록 — 최근 대화순."""
    items, total = await sessions.list_for_user(session, user.id, page=page, size=size)
    return ChatSessionList(
        items=[_session_item(r) for r in items], total=total, page=page, size=size
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_session(
    session_id: int, user: CurrentUser, session: DbSession
) -> ChatSessionDetail:
    """대화 한 건 — 메시지 전체를 포함한다."""
    try:
        chat = await sessions.get_owned(session, user.id, session_id)
    except ChatSessionError as exc:
        raise _http_error(exc) from exc
    rows = await sessions.messages(session, chat.id)
    return ChatSessionDetail(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at.isoformat(),
        last_message_at=(
            chat.last_message_at.isoformat() if chat.last_message_at else None
        ),
        messages=[_message_item(r) for r in rows],
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionItem)
async def rename_session(
    session_id: int,
    payload: ChatSessionRenameRequest,
    user: CurrentUser,
    session: DbSession,
) -> ChatSessionItem:
    try:
        row = await sessions.rename(session, user.id, session_id, payload.title)
    except ChatSessionError as exc:
        raise _http_error(exc) from exc
    return _session_item(row)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: int, user: CurrentUser, session: DbSession
) -> None:
    try:
        await sessions.soft_delete(session, user.id, session_id)
    except ChatSessionError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/messages", response_model=ChatAskResponse)
async def ask(
    session_id: int,
    payload: ChatAskRequest,
    user: CurrentUser,
    session: DbSession,
) -> ChatAskResponse:
    """질문하고 답을 받는다.

    답변의 **형태**는 모델이 정한다 — 마지막에 부른 렌더 툴(`answer_text`·`answer_comparison`)의
    인자가 곧 아티팩트이고, 프론트는 `kind` 로 렌더러를 고른다(services/chat/artifacts.py).

    질문과 답변은 **한 트랜잭션**으로 저장된다. 모델 호출이 실패하면 아무것도 남지 않는다 —
    답 없는 질문만 대화에 남으면 사용자가 지울 수도 다시 물을 수도 없다.
    """
    _require_enabled()
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="질문이 비어 있습니다.")

    try:
        chat = await sessions.get_owned(session, user.id, session_id)
    except ChatSessionError as exc:
        raise _http_error(exc) from exc

    history = await sessions.history_for_agent(
        session, chat.id, turns=settings.chat_history_turns
    )
    question_row = await sessions.append_question(session, chat, question)

    try:
        result = await agent.run(
            session, get_storage(), question=question, history=history
        )
    except LLMRequestError as exc:
        # 모델이 **우리 요청**을 거부했다. 서버 탓으로 말하면 원인을 못 찾는다 — 실제로 이
        # 자리의 "연결할 수 없습니다"가 프롬프트 컨텍스트 초과를 가리고 있었다(2026-07-30).
        await session.rollback()
        log.error("chat_ask_bad_request", user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="질의를 구성하지 못했습니다. 관리자에게 문의해 주세요.",
        ) from exc
    except LLMError as exc:
        await session.rollback()
        log.warning("chat_ask_llm_failed", user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="모델 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc

    answer_row = await sessions.append_answer(
        session,
        chat,
        sessions.AssistantTurn(
            content=result.content,
            artifact=result.artifact,
            citations=result.citations,
            tool_trace=result.tool_trace,
        ),
    )
    await session.commit()
    await session.refresh(question_row)
    await session.refresh(answer_row)

    return ChatAskResponse(
        question=_message_item(question_row),
        answer=_message_item(answer_row),
        searched_documents=result.searched_documents,
        examined_documents=result.examined_documents,
    )
