"""대화형 질의 API 요청·응답 스키마.

**아티팩트는 느슨하게 타이핑한다.** `artifact` 를 판별 유니온으로 못 박지 않고 `dict` 로
둔 이유는, 형태를 하나 늘리는 일이 `services/chat/artifacts.py` 의 `RENDERERS` 한 줄과 프론트
렌더러 하나로 끝나야 하기 때문이다. 여기까지 유니온을 박으면 차트를 붙일 때 세 곳을 고치게
된다. 대신 `kind` 가 항상 있다는 것만 보장하고(서버가 `artifacts.build` 로 만든다) 형태별
검증은 그 모듈이 쥔다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatSessionCreateRequest(BaseModel):
    """새 대화 (POST /api/chat/sessions). 제목은 첫 질문에서 자동으로 만들어진다."""

    title: str = ""


class ChatSessionRenameRequest(BaseModel):
    """제목 변경 (PATCH /api/chat/sessions/{id})."""

    title: str


class ChatSessionItem(BaseModel):
    """목록 한 줄."""

    id: int
    title: str
    created_at: str
    # 아직 대화가 없으면 null — 화면이 "새 대화"로 표시한다.
    last_message_at: str | None = None


class ChatSessionList(BaseModel):
    items: list[ChatSessionItem]
    total: int
    page: int
    size: int


class ChatCitation(BaseModel):
    """답변 근거. 누르면 그 파일의 해당 줄로 이동한다.

    앵커가 페이지가 아니라 **줄 번호**인 것은 md 트리의 좌표가 line_num 이기 때문이다
    (`schemas/wiki.py` 의 WikiCitation 과 같은 모양 — 프론트가 같은 컴포넌트로 그린다).
    """

    file_id: int
    file_name: str
    node_id: str
    node_title: str
    line_num: int


class ChatToolCall(BaseModel):
    """모델이 부른 툴 한 번. 답이 이상할 때 원인은 대개 검색이 무엇을 가져왔는가에 있다."""

    name: str
    # 검색 툴이면 실제로 보낸 질의. 대화 맥락이 어떻게 독립형 질의로 바뀌었는지가 여기 보인다.
    query: str = ""
    # 그 호출이 찾아낸 발췌 수. 0 이면 검색은 됐으나 걸린 게 없다는 뜻이다.
    excerpts: int = 0


class ChatMessageItem(BaseModel):
    """대화 안의 메시지 한 건."""

    id: int
    role: str
    content: str
    # 어시스턴트 메시지에만 있다. `kind` 로 프론트 렌더러가 갈린다.
    artifact: dict[str, Any] | None = None
    citations: list[ChatCitation] = Field(default_factory=list)
    tool_trace: list[ChatToolCall] = Field(default_factory=list)
    created_at: str


class ChatSessionDetail(BaseModel):
    id: int
    title: str
    created_at: str
    last_message_at: str | None = None
    messages: list[ChatMessageItem]


class ChatAskRequest(BaseModel):
    """질문 (POST /api/chat/sessions/{id}/messages)."""

    question: str


class ChatAskResponse(BaseModel):
    """질문과 답변을 **둘 다** 돌려준다.

    질문 메시지도 서버가 저장하며 id 를 갖는다. 프론트가 낙관적으로 그려 둔 임시 메시지를
    서버 것으로 갈아 끼우려면 그 id 가 필요하다.
    """

    question: ChatMessageItem
    answer: ChatMessageItem
    # 질의 대상 문서 전체 / 실제로 트리를 들여다본 문서 수. 좁혀 봤다는 사실이 화면에 드러나야
    # "없다"는 답을 과신하지 않는다(schemas/wiki.py 의 같은 필드와 같은 이유).
    searched_documents: int = 0
    examined_documents: int = 0


__all__ = [
    "ChatAskRequest",
    "ChatAskResponse",
    "ChatCitation",
    "ChatMessageItem",
    "ChatSessionCreateRequest",
    "ChatSessionDetail",
    "ChatSessionItem",
    "ChatSessionList",
    "ChatSessionRenameRequest",
    "ChatToolCall",
]
