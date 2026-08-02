"""대화형 질의의 툴 루프 단위 테스트 — LLM 없이 대본으로 돌린다.

검증 축: 렌더 툴 호출 시 종료와 아티팩트 추출 / 검색 툴 왕복 / 왕복 상한 도달 시 finalize 로
답을 받아 내는가 / 상한에 걸린 보류 호출에 ToolMessage 가 붙는가(안 붙으면 다수 provider 가
400 을 낸다) / 평문으로 끝난 경우의 폴백 / 근거 중복 제거.

실제 모델 호출·검색은 통합 테스트(integration_chat)에서 확인한다. 여기서는 **그래프의
분기**만 본다 — 대본이 있으면 모델이 붙지 않아도 전부 검증된다.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.services import llm
from app.services.chat import agent, artifacts, tools


class _ScriptedClient:
    """`ainvoke` 가 부를 때마다 대본의 다음 메시지를 돌려준다."""

    def __init__(self, script: list[AIMessage], seen: list[list[Any]]) -> None:
        self._script = script
        self._seen = seen

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self._seen.append(list(messages))
        if not self._script:
            return AIMessage(content="대본 소진")
        return self._script.pop(0)


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch):
    """`llm.chat_client` 를 대본으로 갈아 끼운다. 반환값으로 프롬프트 이력을 들여다본다."""

    def install(script: list[AIMessage]) -> dict[str, Any]:
        state: dict[str, Any] = {"prompts": [], "bound": []}

        def fake_client(*, tools: list[Any] | None = None) -> _ScriptedClient:
            state["bound"].append([t["function"]["name"] for t in (tools or [])])
            return _ScriptedClient(script, state["prompts"])

        monkeypatch.setattr(llm, "chat_client", fake_client)
        return state

    return install


def _search_call(query: str, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": tools.SEARCH_WIKI, "args": {"query": query}, "id": call_id}
        ],
    )


def _text_call(markdown: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "answer_text", "args": {"markdown": markdown}, "id": "r1"}],
    )


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch):
    """`tools.run_search` 를 고정 결과로 갈아 끼운다. 호출된 질의를 기록한다."""
    calls: list[str] = []

    async def run(_session: Any, _storage: Any, query: str) -> tools.SearchOutcome:
        calls.append(query)
        return tools.SearchOutcome(
            text=f"[발췌] {query}",
            citations=[
                {
                    "file_id": 7,
                    "file_name": "계획.md",
                    "node_id": "1",
                    "node_title": "개요",
                    "line_num": 3,
                }
            ],
            searched_documents=12,
            examined_documents=9,
        )

    monkeypatch.setattr(tools, "run_search", run)
    return calls


async def test_render_tool_ends_loop_and_becomes_artifact(scripted, fake_search):
    """렌더 툴이 호출되면 루프가 끝나고 그 **인자**가 아티팩트가 된다."""
    scripted([_text_call("연구소 계획은 …")])

    result = await agent.run(None, None, question="계획 알려줘", history=[])

    assert result.artifact == {"kind": "text", "markdown": "연구소 계획은 …"}
    assert result.content == "연구소 계획은 …"
    # 검색 없이 바로 답했으므로 툴 흔적도 근거도 없다.
    assert result.tool_trace == []
    assert fake_search == []


async def test_search_then_answer_collects_citations(scripted, fake_search):
    """검색 → 모델 → 렌더 툴. 근거와 툴 흔적이 쌓인다."""
    scripted([_search_call("2026 연구소 계획"), _text_call("계획은 …")])

    result = await agent.run(None, None, question="계획 알려줘", history=[])

    assert fake_search == ["2026 연구소 계획"]
    assert result.tool_trace == [
        {"name": tools.SEARCH_WIKI, "query": "2026 연구소 계획", "excerpts": 1}
    ]
    assert [c["file_id"] for c in result.citations] == [7]
    assert result.searched_documents == 12
    assert result.examined_documents == 9


async def test_duplicate_citations_are_deduped(scripted, fake_search):
    """같은 절이 여러 번 걸려도 근거 목록에는 한 번만 남는다."""
    scripted(
        [
            _search_call("계획", "a"),
            _search_call("계획 다시", "b"),
            _text_call("답"),
        ]
    )

    result = await agent.run(None, None, question="q", history=[])

    assert len(fake_search) == 2
    assert len(result.citations) == 1


async def test_comparison_artifact_is_clipped_and_padded(scripted, fake_search):
    """비교표는 열 수에 맞춰 행을 다듬는다 — 모델이 짧은 행을 보내는 일이 흔하다."""
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "answer_comparison",
                        "args": {
                            "columns": ["항목", "2025", "2026"],
                            "rows": [["예산", "10억"], ["인원", "5", "7", "군더더기"]],
                            "title": "연도 대조",
                        },
                        "id": "r1",
                    }
                ],
            )
        ]
    )

    result = await agent.run(None, None, question="비교해줘", history=[])

    assert result.artifact["kind"] == "comparison"
    assert result.artifact["rows"] == [["예산", "10억", ""], ["인원", "5", "7"]]
    assert result.artifact["title"] == "연도 대조"


async def test_too_many_rows_are_clipped_and_disclosed(scripted, fake_search):
    """행을 자르면 **자른 사실을 note 에 남긴다** — 조용히 자르면 완전한 표로 읽힌다."""
    rows = [[f"항목{i}", str(i)] for i in range(artifacts.MAX_ROWS + 5)]
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "answer_comparison",
                        "args": {"columns": ["이름", "값"], "rows": rows},
                        "id": "r1",
                    }
                ],
            )
        ]
    )

    result = await agent.run(None, None, question="q", history=[])

    assert len(result.artifact["rows"]) == artifacts.MAX_ROWS
    assert "5행을 줄였습니다" in result.artifact["note"]


async def test_plain_text_ending_falls_back_to_text_artifact(scripted, fake_search):
    """모델이 렌더 툴 없이 평문으로 끝내는 것은 **흔한 경로**다(실측). 답을 버리지 않는다."""
    scripted([AIMessage(content="그냥 평문 답변")])

    result = await agent.run(None, None, question="q", history=[])

    assert result.artifact == {"kind": "text", "markdown": "그냥 평문 답변"}


def test_message_text_strips_thinking_blocks():
    """reasoning 모델의 블록 리스트에서 **추론 트레이스를 버리고** 텍스트만 남긴다.

    회귀 방지 — 실측(Solar-Open2, 2026-08-02)에서 content 가 문자열이 아니라
    [{"type":"thinking",...},{"type":"text",...}] 로 왔고, 이걸 str() 로 감싸면 파이썬 리스트
    리터럴이 그대로 답변이 되면서 그 안에 모델의 사고 과정이 사용자에게 노출된다.
    """
    blocks = [
        {"type": "thinking", "thinking": "사용자는 P1 을 묻고 있다. 아마 신입인 듯"},
        {"type": "text", "text": "P1 은 즉시 전사 공지합니다."},
    ]
    assert agent.message_text(blocks) == "P1 은 즉시 전사 공지합니다."
    # 순수 문자열도 그대로 통과한다.
    assert agent.message_text("평문") == "평문"
    assert agent.message_text(None) == ""


async def test_reasoning_blocks_do_not_leak_into_artifact(scripted, fake_search):
    """폴백 경로에서도 thinking 이 새지 않는다 — 실제로 새던 자리다."""
    scripted(
        [
            AIMessage(
                content=[
                    {"type": "thinking", "thinking": "내부 추론 — 노출되면 안 된다"},
                    {"type": "text", "text": "P1 은 즉시 전사 공지합니다."},
                ]
            )
        ]
    )

    result = await agent.run(None, None, question="q", history=[])

    assert result.artifact == {"kind": "text", "markdown": "P1 은 즉시 전사 공지합니다."}
    assert "추론" not in result.content
    assert "type" not in result.content  # 리스트 리터럴이 통째로 실리던 증상


async def test_invalid_render_args_fall_back_to_text(scripted, fake_search):
    """인자가 스키마에 안 맞으면 표를 포기하되 답은 살린다."""
    scripted(
        [
            AIMessage(
                content="표로 정리했습니다",
                tool_calls=[
                    # columns 가 문자열 — 스키마는 list[str] 를 요구한다.
                    {
                        "name": "answer_comparison",
                        "args": {"columns": "항목", "rows": []},
                        "id": "r1",
                    }
                ],
            )
        ]
    )

    result = await agent.run(None, None, question="q", history=[])

    assert result.artifact == {"kind": "text", "markdown": "표로 정리했습니다"}


async def test_iteration_cap_forces_finalize(
    scripted, fake_search, monkeypatch: pytest.MonkeyPatch
):
    """상한에 걸리면 검색을 멈추고 답을 받아 낸다. 검색 툴은 더 이상 바인딩하지 않는다."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "chat_max_tool_iterations", 2)
    state = scripted(
        [
            _search_call("첫 검색", "a"),
            _search_call("둘째 검색", "b"),
            _text_call("상한에서 정리한 답"),
        ]
    )

    result = await agent.run(None, None, question="q", history=[])

    assert result.artifact == {"kind": "text", "markdown": "상한에서 정리한 답"}
    # 검색은 상한 전까지만 실행됐다 — 둘째 호출은 보류되고 실행되지 않는다.
    assert fake_search == ["첫 검색"]
    # 마지막 호출에는 검색 툴이 빠져 있다(모델이 더 찾으러 갈 길이 없다).
    assert tools.SEARCH_WIKI not in state["bound"][-1]
    assert "answer_text" in state["bound"][-1]


async def test_pending_calls_get_tool_messages_at_cap(
    scripted, fake_search, monkeypatch: pytest.MonkeyPatch
):
    """보류된 검색 호출에 ToolMessage 가 붙는다.

    붙지 않으면 "tool_calls 에 대응하는 결과가 없다"며 다수 provider 가 400 을 내고, 상한에
    걸린 대화가 전부 "질의를 구성하지 못했습니다"로 끝난다.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "chat_max_tool_iterations", 1)
    state = scripted([_search_call("보류될 검색", "pending-1"), _text_call("답")])

    await agent.run(None, None, question="q", history=[])

    finalize_prompt = state["prompts"][-1]
    replies = [m for m in finalize_prompt if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in replies] == ["pending-1"]


async def test_history_is_flattened_to_plain_messages(scripted, fake_search):
    """지난 턴은 평문으로만 넣는다 — 발췌까지 다시 실으면 프롬프트가 턴마다 부푼다."""
    from types import SimpleNamespace

    history = [
        SimpleNamespace(role="user", content="작년 계획은?"),
        SimpleNamespace(role="assistant", content="2025년 계획은 …"),
    ]
    state = scripted([_text_call("올해는 …")])

    await agent.run(None, None, question="올해는?", history=history)  # type: ignore[arg-type]

    contents = [m.content for m in state["prompts"][0]]
    assert "작년 계획은?" in contents
    assert "2025년 계획은 …" in contents
    assert contents[-1] == "올해는?"
