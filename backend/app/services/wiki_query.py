"""위키 질의 — 트리 검색과 근거 반환 (spec/wiki-index.md).

흐름:

    질의 → 질의 대상 트리 수집 → **후보 노드 선별(질문 키워드 점수 + 문자 예산)**
         → LLM 이 관련 노드 선택 → 선택 노드 본문을 원문에서 잘라 컨텍스트 구성 → 답변 + 근거

원칙 넷:

- **프롬프트는 예산 안에 들어간다.** 처음 구현은 대상 트리를 **전부** 노드 선택 프롬프트에
  넣었다. `MAX_CONTEXT_NODES` 는 *선택된* 노드 수만 제한하고 넣는 쪽은 제한하지 않았다.
  문서 20건에서는 동작하고 467건에서는 모든 질의가 죽는다 — 실측 932,753자, vLLM 이
  HTTP 400(`maximum context length is 131072 tokens`)을 돌려줬다. 예산이 이 모듈의 1급 관심사다.
- **좁히는 축은 관련성이다.** "최근 50건만" 같은 문서 수 상한은 구현이 쉽지만 나머지를 영구히
  답변에서 지운다. 위키의 목적이 자료 발견이므로 질문과의 관련성으로 순위를 매기고 꼬리를 자른다.
- **노드 선택에는 title+summary 만 넣는다.** 실측에서 그것만으로 3/3 정확했고, 본문을 넣으면
  트리 수십 개가 컨텍스트를 채워 애초에 들어가지 않는다.
- **존재를 부정하지 않는다.** 못 찾았을 때 "그런 자료는 없습니다"가 아니라 **"찾아본 자료
  중에는 없습니다"**로 답한다. 부정 자체가 정보이고, 실제로 있는데 없다고 말하면 사용자를
  오도한다. 문구가 "접근 가능한"에서 "찾아본"으로 바뀐 이유는 이제 좁히는 축이 권한이 아니라
  관련성이기 때문이다 — 예산에서 잘려 못 본 문서가 있을 수 있고, 그게 사실이다.

권한 판정은 하지 않는다 — 인덱싱된 문서는 전 직원 공개다(`services/wiki.py` 의 불변식).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import File, WikiDocument
from app.services import wiki as wiki_service
from app.services import wiki_llm
from app.services.storage import StorageService
from app.services.wiki_convert import html_to_markdown

log = get_logger(__name__)

# 한 질의에 컨텍스트로 넣을 노드 수 상한. 늘릴수록 답변 근거는 늘지만 지연과 토큰이 커진다.
MAX_CONTEXT_NODES = 6
# 노드 하나에서 가져올 본문 길이 상한 — 아주 긴 절이 컨텍스트를 독점하지 않게 한다.
MAX_NODE_CHARS = 4000
# 노드 선택 프롬프트에 실을 요약 길이. 절을 대표하기에 충분하고, 예산 안에 많은 노드가 들어간다.
SUMMARY_CHARS = 200

# 질문에서 키워드를 뽑을 때 떼는 한국어 조사·어미. 형태소 분석기를 붙이지 않는 이유는
# 이 단계가 **후보 순위**일 뿐이라서다 — 최종 판단은 LLM 이 하고, 놓친 후보의 대가는
# 순위가 조금 나빠지는 것이다. 사전 하나로 얻는 정확도가 의존성 하나보다 값싸다.
_PARTICLES = (
    "으로서", "으로써", "에서는", "에게서", "이라는", "라는", "에서", "에게", "으로", "까지",
    "부터", "보다", "처럼", "만큼", "이나", "나마", "라도", "이란", "란", "은", "는", "이",
    "가", "을", "를", "의", "에", "와", "과", "도", "로", "만", "야", "요",
)
# 질문에 흔히 섞이지만 문서를 가르지 못하는 말. 이것들이 점수를 지배하면 순위가 무의미해진다.
# **관찰에서 늘린다.** 이론적으로 완전한 불용어 사전을 만들려 들 필요가 없다 — 남은 잡음은
# 점수 0 을 받아 순위 꼬리로 밀리고, 잘못 들어가면 진짜 키워드를 지워 순위가 나빠진다.
_STOPWORDS = frozenset(
    {
        "무엇", "어디", "언제", "누구", "어떻게", "어떤", "왜", "얼마", "알려", "알려줘",
        "설명", "정리", "관련", "내용", "문서", "자료", "대해", "대한", "있나", "있는",
        "하는", "해줘", "주세요", "부탁", "그리고", "또는", "정도", "경우", "방법", "이거",
        "저거", "그거", "우리", "지금",
        # 의문형 어미 — 질문이면 거의 항상 붙지만 문서를 가르는 힘이 없다.
        "되나", "되는", "된다", "인가", "인지", "맞나", "될까", "할까", "하나", "뭔가",
    }
)

_SELECT_PROMPT = """다음은 사내 위키 문서들의 목차 트리다.
질문에 답하는 데 필요한 노드를 고르시오.

질문: {question}

문서 트리:
{tree}

아래 JSON 형식으로만 답하시오. 다른 말은 쓰지 마시오.
{{"thinking": "<어떤 근거로 골랐는지>", "nodes": ["<doc_id>:<node_id>", ...]}}

- 관련 노드가 없으면 nodes 를 빈 배열로 두시오.
- 최대 {limit}개까지 고르시오."""

_ANSWER_PROMPT = """다음 발췌만을 근거로 질문에 한국어로 답하시오.

질문: {question}

발췌:
{context}

규칙:
- 발췌에 없는 내용을 지어내지 마시오.
- 발췌로 답할 수 없으면 "찾아본 자료 중에는 없습니다"라고만 답하시오.
- 어느 문서에서 나온 내용인지 문장에 자연스럽게 밝히시오.
- 답변만 출력하시오."""


@dataclass(frozen=True)
class Citation:
    file_id: int
    file_name: str
    node_id: str
    node_title: str
    line_num: int


@dataclass(frozen=True)
class QueryResult:
    answer: str
    citations: list[Citation]
    # 질의 대상 전체 / 이번 질문에서 실제로 트리를 들여다본 문서 수. 둘이 다를 수 있다는
    # 사실이 화면에 드러나야 한다 — 좁혀 본 것을 "전부 뒤졌다"로 읽으면 "없다"를 과신한다.
    searched_documents: int
    examined_documents: int = 0
    thinking: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """검색 단계의 결과 — 답변 생성 **직전**까지.

    단발 질의(`ask`)와 대화형 질의의 `search_wiki` 툴이 이것을 공유한다. 예산 규율(문자
    예산·노드 상한·본문 길이 상한)이 이 한 경로에만 있어야 문서가 늘었을 때 한쪽만 죽는 일이
    생기지 않는다.
    """

    blocks: list[str]
    citations: list[Citation]
    searched_documents: int
    examined_documents: int = 0
    thinking: str | None = None


def _iter_nodes(nodes: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        out.append(n)
        children = n.get("nodes")
        if isinstance(children, list):
            out.extend(_iter_nodes(children))
    return out


async def _queryable_documents(
    session: AsyncSession,
) -> list[tuple[WikiDocument, File]]:
    """질의 대상 문서 — 트리가 준비된 것만.

    권한 판정을 하지 않는다. 인덱싱된 문서는 전 직원 공개라는 불변식(`services/wiki.py`)이
    있고, `@전사` 는 활성 사용자 전원을 포함하므로 판정이 항상 통과한다.
    """
    return list(
        (
            await session.execute(
                select(WikiDocument, File)
                .join(File, File.id == WikiDocument.file_id)
                .where(
                    File.is_deleted.is_(False),
                    WikiDocument.status.in_(wiki_service.QUERYABLE_STATUSES),
                    WikiDocument.tree.is_not(None),
                )
                .order_by(File.name.asc())
            )
        ).all()
    )


def _keywords(question: str) -> list[str]:
    """질문에서 후보 점수용 키워드를 뽑는다 — 2자 이상, 조사 제거, 불용어 제외."""
    out: list[str] = []
    for raw in re.split(r"[^0-9A-Za-z가-힣]+", question):
        token = raw.strip().lower()
        if len(token) < 2:
            continue
        # 조사를 떼되 어간이 2자 미만으로 줄면 원형을 쓴다 — "키가" 에서 "키" 만 남으면
        # 아무 문서에나 걸린다.
        for particle in _PARTICLES:
            if token.endswith(particle) and len(token) - len(particle) >= 2:
                token = token[: -len(particle)]
                break
        if token in _STOPWORDS or len(token) < 2:
            continue
        if token not in out:
            out.append(token)
    return out


@dataclass(frozen=True)
class _Candidate:
    """후보 노드 하나 — 점수와 렌더에 필요한 최소 정보."""

    file_id: int
    file_name: str
    score: int
    depth: int
    line_num: int
    line: str  # 프롬프트에 들어갈 완성된 한 줄


def _candidates(
    docs: list[tuple[WikiDocument, File]], keywords: list[str]
) -> list[_Candidate]:
    """모든 노드에 질문 관련성 점수를 매긴다.

    가중치는 신호의 강도 순이다 — 제목이 가장 강하고(절의 주제 그 자체), 문서명은 그다음
    (문서 전체의 주제라 절 하나를 특정하지는 못한다), 요약은 넓게 걸리므로 가장 약하다.
    """
    out: list[_Candidate] = []
    for doc, file in docs:
        name_hits = sum(1 for k in keywords if k in file.name.lower())
        for depth, node in _iter_nodes_with_depth((doc.tree or {}).get("structure", [])):
            title = str(node.get("title") or "")
            summary = (node.get("summary") or "").replace("\n", " ")[:SUMMARY_CHARS]
            haystack_title = title.lower()
            haystack_summary = summary.lower()
            score = (
                3 * sum(1 for k in keywords if k in haystack_title)
                + 2 * name_hits
                + sum(1 for k in keywords if k in haystack_summary)
            )
            node_id = node.get("node_id", "?")
            tail = f" :: {summary}" if summary else ""
            out.append(
                _Candidate(
                    file_id=file.id,
                    file_name=file.name,
                    score=score,
                    depth=depth,
                    line_num=int(node.get("line_num", 1) or 1),
                    line=f"  - {file.id}:{node_id} {title}{tail}",
                )
            )
    return out


def _iter_nodes_with_depth(
    nodes: list[Any], depth: int = 0
) -> list[tuple[int, dict[str, Any]]]:
    """노드를 깊이와 함께 평탄화한다. 깊이는 키워드가 없을 때의 폴백 순위에 쓴다."""
    out: list[tuple[int, dict[str, Any]]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        out.append((depth, n))
        children = n.get("nodes")
        if isinstance(children, list):
            out.extend(_iter_nodes_with_depth(children, depth + 1))
    return out


def _render_catalog(
    docs: list[tuple[WikiDocument, File]], keywords: list[str], budget: int
) -> tuple[str, int, int]:
    """노드 선택 프롬프트에 넣을 축약 트리. 반환: (트리, 실린 문서 수, 잘라낸 노드 수).

    관련성 순으로 채우고 **예산에서 멈춘다.** 예산이 이 함수 안에 있는 것이 중요하다 —
    점수 로직이 나중에 어떻게 바뀌어도 프롬프트가 컨텍스트를 넘지 않는다는 보장은 여기 남는다.

    키워드 매칭이 하나도 없으면(점수 전부 0) 얕은 노드부터 넣는다. 최상위 절이 문서를 가장
    잘 대표하므로, 예산 안에서 **넓게 훑는** 쪽이 한 문서를 깊게 파는 것보다 낫다.
    본문은 넣지 않는다 — 트리 수십 개면 애초에 들어가지 않는다.
    """
    ranked = sorted(
        _candidates(docs, keywords),
        # 점수 내림차순 → 얕은 것 우선 → 문서·줄 순서(같은 문서의 절이 흩어지지 않게)
        key=lambda c: (-c.score, c.depth, c.file_id, c.line_num),
    )

    used = 0
    picked: list[_Candidate] = []
    dropped = 0
    for cand in ranked:
        # 문서 머리글("[문서 12] 이름")도 예산에 든다 — 새 문서를 열 때만 든다고 보고 넉넉히 잡는다.
        cost = len(cand.line) + 1 + len(cand.file_name) + 16
        if used + cost > budget:
            dropped += 1
            continue
        used += cost
        picked.append(cand)

    # 문서별로 묶어 렌더한다. 같은 문서의 절이 흩어지면 모델이 문서 경계를 잃는다.
    by_doc: dict[int, list[_Candidate]] = {}
    for cand in picked:
        by_doc.setdefault(cand.file_id, []).append(cand)

    lines: list[str] = []
    for file_id, cands in by_doc.items():
        lines.append(f"[문서 {file_id}] {cands[0].file_name}")
        lines.extend(c.line for c in sorted(cands, key=lambda c: c.line_num))
    return "\n".join(lines), len(by_doc), dropped


def _parse_selection(raw: str) -> tuple[list[str], str | None]:
    text = raw.strip()
    if "```" in text:
        # 모델이 코드펜스로 감싸는 경우가 있다.
        parts = text.split("```")
        text = max(parts, key=len)
        text = text[4:] if text.lower().startswith("json") else text
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        return [], None
    nodes = data.get("nodes")
    keys = [str(k) for k in nodes] if isinstance(nodes, list) else []
    return keys, data.get("thinking")


async def _node_body(
    storage: StorageService, file: File, node: dict[str, Any]
) -> str:
    """선택된 노드의 본문을 원문에서 잘라 온다.

    트리에 본문을 담지 않기로 했으므로(원문의 2배가 된다) 질의 시점에 가져온다.
    `line_num`~`end_line` 은 인덱싱 때 만든 마크다운 기준이라, html 은 같은 변환을 다시 태워야
    줄 번호가 맞는다.
    """
    raw = await storage.get_bytes_async(file.file_key)
    text = raw.decode("utf-8", errors="replace")
    if file.name.lower().endswith((".html", ".htm")):
        text = html_to_markdown(text)
    lines = text.split("\n")
    start = max(int(node.get("line_num", 1)) - 1, 0)
    end = min(int(node.get("end_line", len(lines))), len(lines))
    return "\n".join(lines[start:end]).strip()[:MAX_NODE_CHARS]


async def search(
    session: AsyncSession, storage: StorageService, question: str
) -> SearchResult:
    """관련 절을 찾아 본문 발췌와 근거를 돌려준다 — **답변 생성은 하지 않는다.**

    단발 질의와 대화형 질의가 공유하는 검색 경로다. 답변을 여기서 만들지 않는 이유는 대화형
    쪽이 발췌를 받아 형태(텍스트·비교표…)까지 스스로 정하기 때문이다.
    """
    docs = await _queryable_documents(session)
    if not docs:
        return SearchResult(blocks=[], citations=[], searched_documents=0)

    keywords = _keywords(question)
    budget = get_settings().wiki_query_catalog_budget_chars
    catalog, examined, dropped = _render_catalog(docs, keywords, budget)
    if dropped:
        # 조용히 자르면 "왜 그 문서를 못 찾았는지" 를 추적할 수 없다. 자른 사실이 로그에 남아야
        # 예산·점수 가중치를 조정할 근거가 생긴다.
        log.info(
            "wiki_query_catalog_truncated",
            documents=len(docs),
            examined_documents=examined,
            dropped_nodes=dropped,
            keywords=keywords,
            budget_chars=budget,
        )

    raw = await wiki_llm.complete(
        _SELECT_PROMPT.format(
            question=question, tree=catalog, limit=MAX_CONTEXT_NODES
        )
    )
    keys, thinking = _parse_selection(raw)

    by_id = {file.id: (doc, file) for doc, file in docs}
    picked: list[tuple[File, dict[str, Any]]] = []
    for key in keys[:MAX_CONTEXT_NODES]:
        doc_part, _, node_part = key.partition(":")
        try:
            entry = by_id[int(doc_part)]
        except (KeyError, ValueError):
            continue
        doc, file = entry
        for node in _iter_nodes((doc.tree or {}).get("structure", [])):
            if str(node.get("node_id")) == node_part:
                picked.append((file, node))
                break

    blocks: list[str] = []
    citations: list[Citation] = []
    for file, node in picked:
        body = await _node_body(storage, file, node)
        if not body:
            continue
        blocks.append(f"### {file.name} — {node.get('title', '')}\n{body}")
        citations.append(
            Citation(
                file_id=file.id,
                file_name=file.name,
                node_id=str(node.get("node_id", "")),
                node_title=str(node.get("title", "")),
                line_num=int(node.get("line_num", 1)),
            )
        )

    return SearchResult(
        blocks=blocks,
        citations=citations,
        searched_documents=len(docs),
        examined_documents=examined,
        thinking=thinking,
    )


async def ask(
    session: AsyncSession, storage: StorageService, question: str
) -> QueryResult:
    """질의에 답하고 근거를 함께 돌려준다 (단발 `POST /wiki/ask`)."""
    found = await search(session, storage, question)
    if found.searched_documents == 0:
        return QueryResult(
            answer=(
                "아직 위키에 올라온 문서가 없습니다. "
                "드라이브에서 Markdown·HTML 문서의 위키를 켜면 검색 대상이 됩니다."
            ),
            citations=[],
            searched_documents=0,
        )

    if not found.blocks:
        return QueryResult(
            answer="찾아본 자료 중에는 없습니다.",
            citations=[],
            searched_documents=found.searched_documents,
            examined_documents=found.examined_documents,
            thinking=found.thinking,
        )

    answer = await wiki_llm.complete(
        _ANSWER_PROMPT.format(question=question, context="\n\n".join(found.blocks))
    )
    return QueryResult(
        answer=answer,
        citations=found.citations,
        searched_documents=found.searched_documents,
        examined_documents=found.examined_documents,
        thinking=found.thinking,
    )


__all__ = [
    "MAX_CONTEXT_NODES",
    "Citation",
    "QueryResult",
    "SearchResult",
    "ask",
    "search",
]
