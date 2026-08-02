"""채팅 세션·메시지 저장.

권한 판정은 **소유권 하나**다 — 대화는 만든 사람만 본다. 공유 개념이 없으므로 파일 쪽의
권한 계층(`services/permissions.py`)을 끌어오지 않는다.

없는 세션과 남의 세션을 똑같이 404 로 돌려준다. 403 으로 갈라 주면 "그 번호의 대화가
존재한다"는 사실이 새어 나가고, 대화 제목에는 업무 내용이 들어간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, ChatSession
from app.models.enums import ChatRole

# 제목으로 자를 첫 질문의 길이. 목록 한 줄에 들어가면서 무엇에 대한 대화인지는 남는 길이다.
TITLE_CHARS = 60


class ChatSessionError(Exception):
    """세션 조작 실패. HTTP 상태 코드를 함께 전달한다(services/todos.py 관례)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class AssistantTurn:
    """어시스턴트 한 턴의 저장 대상. `agent.run()` 의 결과를 그대로 받는다."""

    content: str
    artifact: dict[str, Any]
    citations: list[dict[str, Any]]
    tool_trace: list[dict[str, Any]]


def derive_title(question: str) -> str:
    """첫 질문에서 제목을 만든다. 사용자가 언제든 고칠 수 있으므로 정교할 필요는 없다."""
    text = " ".join(question.split())
    return text[:TITLE_CHARS] if text else "제목 없음"


async def create(session: AsyncSession, user_id: int, *, title: str = "") -> ChatSession:
    """빈 세션을 만든다. 제목은 첫 질문이 들어올 때 채워진다."""
    row = ChatSession(user_id=user_id, title=title[:TITLE_CHARS])
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_owned(
    session: AsyncSession, user_id: int, session_id: int
) -> ChatSession:
    """소유한 세션을 가져온다. 없거나 남의 것이거나 지운 것이면 404."""
    row = (
        await session.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
                ChatSession.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ChatSessionError(404, "대화를 찾을 수 없습니다.")
    return row


async def list_for_user(
    session: AsyncSession, user_id: int, *, page: int = 1, size: int = 50
) -> tuple[list[ChatSession], int]:
    """최근 대화순 목록. 정렬 키가 last_message_at 인 이유는 models/chat.py 주석 참조."""
    where = (
        ChatSession.user_id == user_id,
        ChatSession.is_deleted.is_(False),
    )
    total = (
        await session.execute(select(func.count()).select_from(ChatSession).where(*where))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(ChatSession)
                .where(*where)
                # 아직 대화가 없는 세션(last_message_at IS NULL)은 방금 만든 것이므로 맨 앞에
                # 온다 — "새 대화" 버튼을 누른 사람이 그 세션을 목록에서 찾지 못하면 안 된다.
                .order_by(ChatSession.last_message_at.desc().nullsfirst(), ChatSession.id.desc())
                .offset(max(page - 1, 0) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def messages(
    session: AsyncSession, session_id: int, *, limit: int | None = None
) -> list[ChatMessage]:
    """대화의 메시지를 시간순으로. `limit` 이 있으면 **뒤에서** 그만큼 가져온다.

    최근 것을 원할 때 앞에서 자르면 대화의 시작만 남는다 — 맥락에 필요한 것은 끝이다.
    """
    stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
    if limit is None:
        rows = (await session.execute(stmt.order_by(ChatMessage.id.asc()))).scalars().all()
        return list(rows)
    tail = (
        (await session.execute(stmt.order_by(ChatMessage.id.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return list(reversed(tail))


async def history_for_agent(
    session: AsyncSession, session_id: int, *, turns: int
) -> list[ChatMessage]:
    """모델에 넘길 최근 대화. 1턴 = 질문+답변이므로 메시지 수는 그 두 배다."""
    if turns <= 0:
        return []
    return await messages(session, session_id, limit=turns * 2)


async def append_question(
    session: AsyncSession, chat: ChatSession, question: str
) -> ChatMessage:
    """사용자 질문을 붙인다. 첫 질문이면 제목도 함께 세운다.

    **커밋하지 않는다.** 질문과 답변은 한 트랜잭션으로 함께 저장된다 — 모델 호출이 실패했을 때
    답 없는 질문만 대화에 남으면, 사용자는 지울 수도 다시 물을 수도 없는 잔해를 보게 된다.
    """
    row = ChatMessage(
        session_id=chat.id, role=ChatRole.USER.value, content=question
    )
    session.add(row)
    if not chat.title:
        chat.title = derive_title(question)
    await session.flush()
    return row


async def append_answer(
    session: AsyncSession, chat: ChatSession, turn: AssistantTurn
) -> ChatMessage:
    """어시스턴트 답변을 붙이고 세션을 목록 맨 위로 올린다."""
    row = ChatMessage(
        session_id=chat.id,
        role=ChatRole.ASSISTANT.value,
        content=turn.content,
        artifact=turn.artifact,
        citations=turn.citations or None,
        tool_trace=turn.tool_trace or None,
    )
    session.add(row)
    # 질문이 아니라 **답변** 시각으로 올린다. 답이 오래 걸리는 질의가 목록에서 위아래로
    # 두 번 움직이지 않는다.
    chat.last_message_at = func.now()
    await session.flush()
    return row


async def rename(
    session: AsyncSession, user_id: int, session_id: int, title: str
) -> ChatSession:
    chat = await get_owned(session, user_id, session_id)
    cleaned = " ".join(title.split())[:TITLE_CHARS]
    if not cleaned:
        raise ChatSessionError(422, "제목이 비어 있습니다.")
    chat.title = cleaned
    await session.commit()
    await session.refresh(chat)
    return chat


async def soft_delete(session: AsyncSession, user_id: int, session_id: int) -> None:
    """소프트 삭제. 하드 삭제하면 메시지까지 CASCADE 로 함께 사라진다."""
    chat = await get_owned(session, user_id, session_id)
    chat.is_deleted = True
    await session.commit()


__all__ = [
    "AssistantTurn",
    "ChatSessionError",
    "append_answer",
    "append_question",
    "create",
    "derive_title",
    "get_owned",
    "history_for_agent",
    "list_for_user",
    "messages",
    "rename",
    "soft_delete",
]
