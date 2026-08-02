"""모델에 노출하는 툴 — 검색 계열과 렌더 계열.

**툴 이름을 명시적으로 정한다.** pydantic 클래스를 그대로 `bind_tools` 에 넘기면 툴 이름이
클래스명(`AnswerText`)이 되는데, 그 이름은 저장된 `tool_trace` 와 프론트 렌더러 키에까지
번진다. OpenAI function 형식의 dict 로 직접 만들어 이름을 `answer_text` 로 고정한다 —
클래스를 리네임해도 저장된 대화가 안 깨진다.

툴을 늘리는 자리도 여기다. 웹서치가 붙으면 `SEARCH_TOOLS` 에 스펙 하나와 실행 분기 하나가
는다. 렌더 계열은 `artifacts.RENDERERS` 에서 자동으로 만들어지므로 손댈 필요가 없다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services import wiki_query
from app.services.chat import artifacts
from app.services.storage import StorageService

log = get_logger(__name__)

SEARCH_WIKI = "search_wiki"

# 한 번의 검색 결과가 프롬프트에 실을 수 있는 길이. `wiki_query` 가 이미 노드당 4,000자·
# 최대 6노드로 자르지만, 툴 루프는 그 결과가 **여러 번** 쌓이는 구조라 상한이 한 겹 더 필요하다.
MAX_TOOL_RESULT_CHARS = 12_000

_SEARCH_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SEARCH_WIKI,
        "description": (
            "사내 위키 문서에서 질문과 관련된 절을 찾아 본문 발췌를 돌려준다. "
            "답하기 전에 반드시 한 번 이상 호출하시오. "
            "여러 대상을 견주어야 하면 대상마다 따로 호출하는 편이 정확하다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "검색할 내용. **앞선 대화를 모르는 사람도 이해할 수 있는 독립형 "
                        "문장으로 쓰시오.** 예를 들어 사용자가 '그거 작년은?'이라고 물었고 "
                        "앞에서 2026년 연구소 계획을 이야기하고 있었다면 "
                        "'2025년 연구소 계획'이라고 쓴다. 대명사·생략을 남기면 검색이 실패한다."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}

SEARCH_TOOLS: dict[str, dict[str, Any]] = {SEARCH_WIKI: _SEARCH_SPEC}


def _render_spec(name: str) -> dict[str, Any]:
    schema = artifacts.RENDERERS[name].model_json_schema()
    # 모델에는 필드 설명만 필요하다. pydantic 의 title/$defs 는 토큰만 먹는다.
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": (artifacts.RENDERERS[name].__doc__ or "").strip(),
            "parameters": schema,
        },
    }


def specs(*, render_only: bool = False) -> list[dict[str, Any]]:
    """모델에 바인딩할 툴 목록.

    `render_only` 는 툴 왕복 상한에 걸렸을 때 쓴다 — 검색을 빼면 모델이 더 찾으러 갈 길이
    없어져 답을 내놓는 것 말고 할 수 있는 일이 없다. 강제 tool_choice 에 기대지 않는 이유는
    provider 마다 지원이 갈리기 때문이다(자체 호스팅 vLLM 은 특히).
    """
    render = [_render_spec(name) for name in artifacts.RENDERERS]
    if render_only:
        return render
    return [*SEARCH_TOOLS.values(), *render]


class SearchOutcome:
    """검색 한 번의 결과. 모델에 돌려줄 본문과, 화면에 붙일 근거를 함께 들고 있다."""

    def __init__(
        self,
        *,
        text: str,
        citations: list[dict[str, Any]],
        searched_documents: int,
        examined_documents: int,
    ) -> None:
        self.text = text
        self.citations = citations
        self.searched_documents = searched_documents
        self.examined_documents = examined_documents


async def run_search(
    session: AsyncSession, storage: StorageService, query: str
) -> SearchOutcome:
    """`search_wiki` 를 실행한다.

    검색 자체는 `wiki_query.search` 를 그대로 쓴다 — 후보 선별의 문자 예산(180,000자)과 노드
    상한이 그 한 경로에만 있어야, 문서가 늘었을 때 단발 질의만 살고 대화형이 죽는 일이 없다.

    **대화 히스토리는 여기로 흘러 들어오지 않는다.** 모델이 만든 독립형 질의만 들어온다
    (툴 설명 참조). 히스토리가 검색 프롬프트에 누적되면 예산 계산이 무의미해진다.
    """
    found = await wiki_query.search(session, storage, query)

    if found.searched_documents == 0:
        return SearchOutcome(
            # **문장을 지정해 준다.** "드라이브에서 위키를 켜라"처럼 여지를 남기면 모델이
            # 빈칸을 채운다 — 실측(2026-08-02)에서 "구글 드라이브에 있는 문서를 연동",
            # "담당자에게 위키 활성화 요청"이라고 지어냈다. 이 제품은 구글 드라이브가 아니고,
            # 위키는 문서 소유자가 직접 켜므로 담당자도 없다. 제품 사실은 모델이 추론할 수
            # 있는 것이 아니라 우리만 아는 것이라, 알려주지 않으면 반드시 지어낸다.
            text=(
                "검색할 수 있는 위키 문서가 하나도 없다. 아래 내용만 전하고 그 밖의 절차나 "
                "담당자, 다른 서비스 이름을 덧붙이지 마시오:\n"
                "「아직 위키에 올라온 문서가 없습니다. 드라이브에서 Markdown·HTML 문서를 열어 "
                "'위키 설정'을 켜면 검색 대상이 됩니다.」"
            ),
            citations=[],
            searched_documents=0,
            examined_documents=0,
        )

    if not found.blocks:
        return SearchOutcome(
            # **존재를 부정하지 않는다.** 모델이 "그런 자료는 없다"로 단정하지 않도록 검색
            # 범위가 좁혀졌다는 사실을 그대로 준다(services/wiki_query.py 모듈 주석).
            text=(
                f"관련된 절을 찾지 못했다. 대상 문서 {found.searched_documents}건 중 "
                f"{found.examined_documents}건의 목차를 살펴본 결과다. "
                "다른 표현으로 다시 검색하거나, 찾아본 자료 중에는 없다고 답하시오."
            ),
            citations=[],
            searched_documents=found.searched_documents,
            examined_documents=found.examined_documents,
        )

    body = "\n\n".join(found.blocks)
    if len(body) > MAX_TOOL_RESULT_CHARS:
        body = body[:MAX_TOOL_RESULT_CHARS] + "\n\n(발췌가 길어 이후는 생략됨)"

    return SearchOutcome(
        text=body,
        citations=[
            {
                "file_id": c.file_id,
                "file_name": c.file_name,
                "node_id": c.node_id,
                "node_title": c.node_title,
                "line_num": c.line_num,
            }
            for c in found.citations
        ],
        searched_documents=found.searched_documents,
        examined_documents=found.examined_documents,
    )


__all__ = [
    "MAX_TOOL_RESULT_CHARS",
    "SEARCH_TOOLS",
    "SEARCH_WIKI",
    "SearchOutcome",
    "run_search",
    "specs",
]
