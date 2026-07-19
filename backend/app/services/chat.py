"""챗 세션/메시지 서비스 (PRD 5.16/6.9, Phase 7-2).

세션·메시지는 **소유 사용자만** 접근 가능하다(공유 불가) — 타인 세션 접근은 존재를 숨기려
404 로 응답한다. 검색·생성 파이프라인은 chat_pipeline 이 담당하고, 이 서비스는 세션 CRUD 와
메시지 영속화(citations JSONB 포함)만 맡는다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, ChatSession, User, WikiSpace
from app.services import wiki as wiki_service


class ChatServiceError(Exception):
    """챗 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# 첫 질문으로 세션 제목을 자동 설정할 때의 최대 길이(chat_sessions.title VARCHAR(200)).
_TITLE_MAX = 200


async def create_session(
    session: AsyncSession,
    user: User,
    title: str = "",
    space_id: int | None = None,
) -> ChatSession:
    """빈 제목을 허용하는 세션 생성. 첫 질문 전송 시 제목이 자동 설정된다.

    space_id 가 주어지면 접근 가능한 위키 스페이스여야 한다(아니면 404) — 이후 이 세션의 검색은
    그 스페이스 범위로 제한된다(PRD 3.7.5).
    """
    if space_id is not None:
        space = await session.get(WikiSpace, space_id)
        if space is None or not await wiki_service.can_access_space(
            session, user, space
        ):
            raise ChatServiceError(404, "위키 스페이스를 찾을 수 없습니다.")
    row = ChatSession(
        user_id=user.id, title=(title or "")[:_TITLE_MAX], space_id=space_id
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_owned_assistant_message(
    session: AsyncSession, user: User, message_id: int
) -> ChatMessage:
    """소유 세션의 assistant 메시지를 조회한다(승격 대상 검증).

    타인/부재는 404(존재 은닉), 사용자(user) 메시지는 400.
    """
    row = (
        await session.execute(
            select(ChatMessage)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(ChatMessage.id == message_id, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ChatServiceError(404, "메시지를 찾을 수 없습니다.")
    if row.role != "assistant":
        raise ChatServiceError(400, "assistant 답변만 위키로 승격할 수 있습니다.")
    return row


async def list_sessions(session: AsyncSession, user: User) -> list[ChatSession]:
    """내 세션 목록(최근 생성순)."""
    rows = (
        await session.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user.id)
            .order_by(ChatSession.created_at.desc(), ChatSession.id.desc())
        )
    ).scalars().all()
    return list(rows)


async def get_owned_session(
    session: AsyncSession, user: User, session_id: int
) -> ChatSession:
    """소유 세션을 조회한다. 없거나 타인 소유면 404(존재 은닉)."""
    row = await session.get(ChatSession, session_id)
    if row is None or row.user_id != user.id:
        raise ChatServiceError(404, "세션을 찾을 수 없습니다.")
    return row


async def get_messages(
    session: AsyncSession, session_id: int
) -> list[ChatMessage]:
    """세션 메시지 히스토리(시간순, citations 포함)."""
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def get_recent_history(
    session: AsyncSession, session_id: int, limit: int
) -> list[tuple[str, str]]:
    """프롬프트에 포함할 세션 최근 N개 메시지를 (role, content) 시간순으로 반환한다."""
    if limit <= 0:
        return []
    rows = (
        await session.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    ).all()
    # 최근순으로 가져와 뒤집어 시간순으로 되돌린다.
    return [(r.role, r.content) for r in reversed(rows)]


async def delete_session(
    session: AsyncSession, user: User, session_id: int
) -> None:
    """소유 세션을 삭제한다(메시지는 FK CASCADE 로 함께 삭제). 타인/부재면 404."""
    row = await get_owned_session(session, user, session_id)
    await session.delete(row)
    await session.commit()


async def add_message(
    session: AsyncSession,
    session_id: int,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    """메시지를 저장한다(assistant 메시지는 citations JSONB 포함). 커밋은 호출자가 조율."""
    row = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        citations=citations,
    )
    session.add(row)
    return row


async def maybe_set_title(
    session: AsyncSession, chat_session: ChatSession, first_question: str
) -> None:
    """제목이 비어 있으면 첫 질문으로 세션 제목을 자동 설정한다(커밋은 호출자)."""
    if chat_session.title:
        return
    title = " ".join(first_question.split())[:_TITLE_MAX]
    chat_session.title = title or "새 대화"
