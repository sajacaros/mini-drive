"""대화형 질의의 툴 루프 (LangGraph).

    model ──┬─ answer_* 호출     → END       (툴 인자가 곧 아티팩트)
            ├─ search_wiki 호출  → tools → model
            ├─ 툴 호출 없음      → END       (평문을 텍스트 아티팩트로 감싼다)
            └─ 왕복 상한 도달    → finalize → END

**평문으로 끝나는 것이 흔한 경로다.** 실측(Solar-Open2, 2026-08-02)에서 모델은 표가 필요할
때만 `answer_comparison` 을 부르고, 일반 답변은 툴 없이 그냥 쓴다. 프롬프트로 "반드시 렌더
툴을 호출하라"고 시켜도 그렇다. 그래서 그 경로를 예외가 아니라 **정상 경로**로 다룬다 —
왕복이 하나 줄어드니 오히려 낫고, 형태가 필요한 답만 툴을 태우는 것이 원래 의도이기도 하다.

**`create_agent` 를 쓰지 않는 이유.** 표준 종료 조건은 "툴 호출이 없는 메시지"인데 우리
규칙은 "`answer_*` 툴이 호출되면 끝"이다. 조건부 엣지 하나로 끝나는 차이라 프리빌트를
비틀기보다 그래프를 드러내 놓는 편이 읽기 쉽고, `ToolNode` + `Command` 로 상태를 주고받을 때
알려진 마찰도 피한다.

**checkpointer 를 붙이지 않는다.** 그래프는 매 턴 stateless 로 돌고 히스토리는 호출자가
`chat_messages` 에서 읽어 넣는다. 근거는 `app/models/chat.py` 의 모듈 주석에 있다.

**아티팩트를 그래프 상태에 심지 않는다.** 루프가 끝난 뒤 메시지 이력에서 마지막 `answer_*`
호출의 인자를 읽는다 — 상태 배관이 없어 실패 지점이 하나 줄고, 형태를 늘려도 상태 정의가
그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import ChatMessage
from app.models.enums import ChatRole
from app.services import llm
from app.services.chat import artifacts, tools
from app.services.storage import StorageService

log = get_logger(__name__)

SYSTEM_PROMPT = """당신은 사내 위키 문서에 근거해 답하는 도우미다.

규칙:
- 답하기 전에 `search_wiki` 로 근거를 찾으시오. 여러 대상을 견주어야 하면 대상마다 따로 검색하시오.
- 발췌에 없는 내용을 지어내지 마시오.
- 발췌로 답할 수 없으면 "찾아본 자료 중에는 없습니다"라고 말하시오. **"그런 자료는 없습니다"라고
  단정하지 마시오** — 검색은 질문과 관련된 문서부터 예산만큼만 훑으므로, 못 찾은 것과 없는 것은
  다르다.
- 어느 문서에서 나온 내용인지 문장에 자연스럽게 밝히시오.

답의 형태:
- 둘 이상을 항목별로 견주는 답이면 `answer_comparison` 을 호출하시오. 연도별·부서별·등급별
  대조가 여기 해당한다. 사용자가 "비교해줘"라고 말하지 않아도, 자료가 견주기 좋은 모양이면
  표로 만드시오. 반대로 견줄 것이 하나뿐이면 표로 만들지 마시오.
- 그 밖에는 한국어 마크다운으로 그냥 답하시오."""

# 왕복 상한에 걸렸을 때 모델에 주는 마지막 지시. 검색 툴을 빼고 이것만 덧붙인다.
_FINALIZE_NUDGE = (
    "검색 횟수 상한에 도달했다. 더 검색하지 말고, 지금까지 받은 발췌만으로 지금 답하시오. "
    "발췌가 부족하면 무엇까지 확인했고 무엇이 부족한지 밝히시오."
)


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # 모델 호출 횟수. 왕복 상한 판정에 쓴다.
    iterations: int


@dataclass
class RunResult:
    """루프 한 번의 결과 — 저장과 응답에 필요한 전부."""

    artifact: dict[str, Any]
    citations: list[dict[str, Any]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    searched_documents: int = 0
    examined_documents: int = 0

    @property
    def content(self) -> str:
        return artifacts.summarize(self.artifact)


def message_text(content: Any) -> str:
    """메시지 본문에서 **사람에게 보일 텍스트만** 뽑는다.

    reasoning 모델은 `content` 를 문자열이 아니라 블록 리스트로 준다:

        [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}]

    이걸 `str()` 로 감싸면 파이썬 리스트 리터럴이 그대로 답변이 되고, 그 안에 추론 트레이스가
    실려 사용자에게 노출된다(실측 2026-08-02). Solar 가 추론을 분리해 보내는 의미가 통째로
    사라지는 자리다.

    **thinking 블록은 버린다.** 그건 모델의 사고 과정이지 답이 아니며, 사내 문서에 대한
    추측이 섞여 있어 답변으로 읽히면 곤란하다.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(p for p in parts if p).strip()


def _render_call(message: AnyMessage) -> dict[str, Any] | None:
    """메시지에 렌더 툴 호출이 있으면 그 호출을 돌려준다."""
    for call in getattr(message, "tool_calls", None) or []:
        if call.get("name") in artifacts.RENDERERS:
            return dict(call)
    return None


def _pending_search_calls(message: AnyMessage) -> list[dict[str, Any]]:
    return [
        dict(c)
        for c in getattr(message, "tool_calls", None) or []
        if c.get("name") not in artifacts.RENDERERS
    ]


def history_messages(rows: list[ChatMessage]) -> list[AnyMessage]:
    """저장된 대화를 모델 입력으로 바꾼다.

    어시스턴트 메시지는 **평문 요약**(`content`)으로 넣고 툴 호출/결과는 넣지 않는다. 지난
    턴의 발췌까지 다시 실으면 턴이 쌓일수록 프롬프트가 선형으로 부풀고, 그 발췌는 이번 질문과
    관련이 없을 수도 있다. 필요하면 모델이 다시 검색한다 — 그게 검색 툴이 있는 이유다.
    """
    out: list[AnyMessage] = []
    for row in rows:
        if row.role == ChatRole.USER.value:
            out.append(HumanMessage(content=row.content))
        else:
            out.append(AIMessage(content=row.content))
    return out


def build_graph(session: AsyncSession, storage: StorageService, sink: RunResult) -> Any:
    """이번 요청 전용 그래프.

    DB 세션과 스토리지를 클로저로 잡는다 — LangChain 의 `InjectedState` 로 툴에 밀어 넣는
    대신 툴 실행을 우리가 직접 하기 때문에 가능한 단순화다. `sink` 에는 근거와 툴 흔적이
    쌓인다(그래프 상태에 넣지 않는 이유는 모듈 주석 참조).
    """
    max_iterations = settings.chat_max_tool_iterations

    async def call_model(state: ChatState) -> dict[str, Any]:
        client = llm.chat_client(tools=tools.specs())
        message = await llm.with_retry(lambda: client.ainvoke(state["messages"]))
        return {"messages": [message], "iterations": state["iterations"] + 1}

    async def call_tools(state: ChatState) -> dict[str, Any]:
        last = state["messages"][-1]
        out: list[AnyMessage] = []
        for call in _pending_search_calls(last):
            name = call.get("name") or ""
            args = call.get("args") or {}
            call_id = str(call.get("id") or "")
            if name != tools.SEARCH_WIKI:
                # 모르는 툴을 부르면 실패를 모델에 돌려준다 — 예외로 요청을 죽이면 사용자는
                # 원인을 알 수 없고, 모델은 대개 다음 턴에 올바른 툴로 고쳐 부른다.
                out.append(
                    ToolMessage(
                        content=f"알 수 없는 툴입니다: {name}", tool_call_id=call_id
                    )
                )
                continue

            query = str(args.get("query") or "").strip()
            if not query:
                out.append(
                    ToolMessage(content="query 가 비어 있습니다.", tool_call_id=call_id)
                )
                continue

            outcome = await tools.run_search(session, storage, query)
            sink.tool_trace.append(
                {
                    "name": name,
                    "query": query,
                    "excerpts": len(outcome.citations),
                }
            )
            # 같은 절이 여러 번 걸릴 수 있다 — 화면의 근거 목록에서는 한 번만 보여야 한다.
            seen = {(c["file_id"], c["node_id"]) for c in sink.citations}
            for citation in outcome.citations:
                key = (citation["file_id"], citation["node_id"])
                if key not in seen:
                    seen.add(key)
                    sink.citations.append(citation)
            # 대상 문서 수는 매 검색마다 같지만, 훑어본 수는 질의마다 다르다. 사용자에게는
            # "이 답을 위해 가장 넓게 훑었을 때 몇 건을 봤는가"가 의미 있는 값이다.
            sink.searched_documents = outcome.searched_documents
            sink.examined_documents = max(
                sink.examined_documents, outcome.examined_documents
            )
            out.append(ToolMessage(content=outcome.text, tool_call_id=call_id))
        return {"messages": out}

    async def finalize(state: ChatState) -> dict[str, Any]:
        """왕복 상한에 걸렸을 때 답을 받아 낸다.

        보류된 검색 호출에 **반드시 ToolMessage 로 응답한다.** 툴 호출에 대응하는 결과가
        없는 메시지 열은 다수 provider 가 400 으로 거절한다 — 상한에 걸린 대화가 전부
        "요청을 구성하지 못했습니다"로 끝나게 된다.
        """
        last = state["messages"][-1]
        closers: list[AnyMessage] = [
            ToolMessage(
                content="검색 횟수 상한에 도달해 실행하지 않았습니다.",
                tool_call_id=str(call.get("id") or ""),
            )
            for call in _pending_search_calls(last)
        ]
        client = llm.chat_client(tools=tools.specs(render_only=True))
        prompt = [*state["messages"], *closers, HumanMessage(content=_FINALIZE_NUDGE)]
        message = await llm.with_retry(lambda: client.ainvoke(prompt))
        return {
            "messages": [*closers, HumanMessage(content=_FINALIZE_NUDGE), message],
            "iterations": state["iterations"] + 1,
        }

    def route(state: ChatState) -> str:
        last = state["messages"][-1]
        if _render_call(last) is not None:
            return END
        if not _pending_search_calls(last):
            # 평문으로 끝냈다 — 예외가 아니라 흔한 경로다(모듈 주석의 실측).
            return END
        if state["iterations"] >= max_iterations:
            return "finalize"
        return "tools"

    graph = StateGraph(ChatState)
    graph.add_node("model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("model")
    graph.add_conditional_edges(
        "model", route, {"tools": "tools", "finalize": "finalize", END: END}
    )
    graph.add_edge("tools", "model")
    graph.add_edge("finalize", END)
    return graph.compile()


def _artifact_from(messages: list[AnyMessage]) -> dict[str, Any]:
    """마지막 렌더 툴 호출을 아티팩트로 바꾼다. 없으면 평문을 텍스트 아티팩트로 감싼다."""
    for message in reversed(messages):
        call = _render_call(message)
        if call is None:
            continue
        try:
            return artifacts.build(str(call.get("name")), dict(call.get("args") or {}))
        except (ValueError, TypeError) as exc:
            # 인자가 스키마에 안 맞는다. 답 자체를 버리기보다 평문으로 떨어뜨린다 —
            # 사용자에게는 표가 아닌 답이라도 있는 편이 낫다.
            log.warning("chat_artifact_invalid", tool=call.get("name"), error=str(exc))
            break

    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = message_text(message.content)
            if text.strip():
                return artifacts.text(text.strip())
    return artifacts.text("답변을 생성하지 못했습니다. 다시 시도해 주세요.")


async def run(
    session: AsyncSession,
    storage: StorageService,
    *,
    question: str,
    history: list[ChatMessage],
) -> RunResult:
    """질문 하나를 처리한다. 예외는 `services/llm.py` 의 것이 그대로 올라간다."""
    sink = RunResult(artifact={})
    graph = build_graph(session, storage, sink)

    inbox: list[AnyMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        *history_messages(history),
        HumanMessage(content=question),
    ]
    # recursion_limit 은 LangGraph 가 무한 루프를 끊는 안전망이다. 우리 상한(왕복 횟수)이
    # 먼저 걸리도록 넉넉히 준다 — 노드 하나가 왕복 하나가 아니라서 2배로는 모자란다.
    final = await graph.ainvoke(
        {"messages": inbox, "iterations": 0},
        {"recursion_limit": settings.chat_max_tool_iterations * 3 + 10},
    )

    sink.artifact = _artifact_from(list(final["messages"]))
    return sink


__all__ = ["SYSTEM_PROMPT", "ChatState", "RunResult", "build_graph", "run"]
