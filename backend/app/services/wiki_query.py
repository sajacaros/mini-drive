"""위키 질의 — 트리 검색과 근거 반환 (spec/wiki-index.md).

흐름:

    질의 → 접근 가능한 트리 수집(권한 필터) → LLM 이 관련 노드 선택
         → 선택 노드 본문을 원문에서 잘라 컨텍스트 구성 → 답변 + 근거

원칙 셋:

- **권한 필터는 질의 대상 선정 단계에 건다.** 답변을 만든 뒤 거르지 않는다 — 그때는 이미
  본문이 모델 컨텍스트에 들어간 뒤라 늦다.
- **노드 선택에는 title+summary 만 넣는다.** 실측에서 그것만으로 3/3 정확했고, 본문을 넣으면
  트리 수십 개가 컨텍스트를 채워 애초에 들어가지 않는다.
- **존재를 부정하지 않는다.** 접근 가능한 자료에서 못 찾았을 때 "그런 자료는 없습니다"가
  아니라 "접근 가능한 자료 중에는 없습니다"로 답한다. 부정 자체가 정보이고, 실제로 있는데
  없다고 말하면 사용자를 오도한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import File, User, WikiDocument
from app.services import permissions as permissions_service
from app.services import wiki as wiki_service
from app.services import wiki_llm
from app.services.storage import StorageService
from app.services.wiki_convert import html_to_markdown

log = get_logger(__name__)

# 한 질의에 컨텍스트로 넣을 노드 수 상한. 늘릴수록 답변 근거는 늘지만 지연과 토큰이 커진다.
MAX_CONTEXT_NODES = 6
# 노드 하나에서 가져올 본문 길이 상한 — 아주 긴 절이 컨텍스트를 독점하지 않게 한다.
MAX_NODE_CHARS = 4000

_SELECT_PROMPT = """다음은 사용자가 접근할 수 있는 문서들의 목차 트리다.
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
- 발췌로 답할 수 없으면 "접근 가능한 자료 중에는 없습니다"라고만 답하시오.
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
    searched_documents: int
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


async def _accessible_documents(
    session: AsyncSession, user: User
) -> list[tuple[WikiDocument, File]]:
    """이 사용자가 접근할 수 있는, 준비된 트리만 모은다 (권한 필터가 여기 있다)."""
    rows = (
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

    out: list[tuple[WikiDocument, File]] = []
    for doc, file in rows:
        if file.user_id != user.id:
            level = await permissions_service.get_access_level(session, user, file)
            if level is None:
                continue
        out.append((doc, file))
    return out


def _render_catalog(docs: list[tuple[WikiDocument, File]]) -> str:
    """노드 선택 프롬프트에 넣을 축약 트리. 본문은 넣지 않는다."""
    lines: list[str] = []
    for doc, file in docs:
        lines.append(f"[문서 {file.id}] {file.name}")
        for node in _iter_nodes((doc.tree or {}).get("structure", [])):
            summary = (node.get("summary") or "").replace("\n", " ")[:200]
            nid = node.get("node_id", "?")
            title = node.get("title", "")
            tail = f" :: {summary}" if summary else ""
            lines.append(f"  - {file.id}:{nid} {title}{tail}")
    return "\n".join(lines)


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


async def ask(
    session: AsyncSession, storage: StorageService, user: User, question: str
) -> QueryResult:
    """질의에 답하고 근거를 함께 돌려준다."""
    docs = await _accessible_documents(session, user)
    if not docs:
        return QueryResult(
            answer=(
                "접근 가능한 자료 중에는 없습니다. "
                "위키에 인덱싱된 문서가 없거나 열람 권한이 없습니다."
            ),
            citations=[],
            searched_documents=0,
        )

    catalog = _render_catalog(docs)
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

    if not picked:
        return QueryResult(
            answer="접근 가능한 자료 중에는 없습니다.",
            citations=[],
            searched_documents=len(docs),
            thinking=thinking,
        )

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

    if not blocks:
        return QueryResult(
            answer="접근 가능한 자료 중에는 없습니다.",
            citations=[],
            searched_documents=len(docs),
            thinking=thinking,
        )

    answer = await wiki_llm.complete(
        _ANSWER_PROMPT.format(question=question, context="\n\n".join(blocks))
    )
    return QueryResult(
        answer=answer,
        citations=citations,
        searched_documents=len(docs),
        thinking=thinking,
    )


__all__ = ["MAX_CONTEXT_NODES", "Citation", "QueryResult", "ask"]
