"""위키 인덱싱 워커 (spec/wiki-index.md).

파이프라인:

    큐 → 대상 재확인 → 본문 로드(MinIO) → (html 이면 md 변환) → 헤더 트리 파싱
      → 노드 요약(LLM, 긴 절만) → **원자적 스왑** → SSE 통지

원칙 셋:

- **인덱싱 중에도 구 트리로 답한다.** 새 트리를 다 만든 뒤 한 번에 갈아끼운다. 문서를 검색에서
  빼면 답변이 누락되고, 부분 갱신 상태를 노출하면 트리 앞뒤가 안 맞는다.
- **실패해도 구 트리를 유지한다.** 조용히 실패해서 사용자가 최신인 줄 아는 상태가 가장 나쁘므로
  status=failed 와 사유를 남긴다.
- **멱등하다.** 같은 파일을 두 번 처리해도 결과가 같다. 큐가 유실·중복돼도 안전하다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import File, WikiDocument
from app.services import wiki as wiki_service
from app.services import wiki_llm, wiki_queue, wiki_tree
from app.services.storage import StorageService
from app.services.wiki_convert import html_to_markdown

log = get_logger(__name__)

_SUMMARY_PROMPT = """다음은 문서의 한 절이다. 이 절이 무엇을 다루는지 한국어 두세 문장으로 요약해라.

규칙:
- 원문에 없는 내용을 지어내지 않는다.
- 목록·표는 항목을 나열하지 말고 무엇에 대한 목록인지 쓴다.
- 요약문만 출력한다. 머리말·꼬리말을 붙이지 않는다.

제목: {title}

본문:
{body}"""

# 요약을 동시에 몇 개까지 보낼지. 인덱싱이 GPU 를 독점하면 대화형 질의 지연이 그대로 커지므로
# 낮게 잡는다 — 배치라 급하지 않다.
_SUMMARY_CONCURRENCY = 3


class IndexError_(Exception):
    """인덱싱 실패. 메시지는 사용자에게 보이는 사유로 쓴다."""


async def _load_markdown(storage: StorageService, file: File) -> str:
    raw = await storage.get_bytes_async(file.file_key)
    if len(raw) > settings.wiki_max_input_bytes:
        raise IndexError_("파일이 인덱싱 상한을 넘습니다.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    name = file.name.lower()
    if name.endswith((".html", ".htm")):
        text = html_to_markdown(text)
        if not text.strip():
            raise IndexError_("HTML 에서 본문을 찾지 못했습니다.")
    return text


async def _summarize(nodes: list[wiki_tree.TreeNode], lines: list[str]) -> None:
    """긴 절만 LLM 으로 요약하고, 짧은 절은 본문을 그대로 요약으로 둔다.

    짧은 절을 요약하면 원문보다 길어지거나 정보가 준다. 검색에는 본문 자체가 더 낫다.
    """
    sem = asyncio.Semaphore(_SUMMARY_CONCURRENCY)

    async def one(node: wiki_tree.TreeNode) -> None:
        body = wiki_tree.own_text(lines, node)
        if not body:
            return
        if len(body) < wiki_tree.SHORT_NODE_CHARS:
            node.summary = body
            return
        prompt = _SUMMARY_PROMPT.format(title=node.title, body=body)
        async with sem:
            try:
                node.summary = await wiki_llm.complete(prompt)
            except wiki_llm.WikiLLMError:
                # 한 노드의 요약 실패가 트리 전체를 버리게 하지 않는다 — 본문 앞부분으로 대신한다.
                node.summary = body[: wiki_tree.SHORT_NODE_CHARS]
                log.warning("wiki_summary_failed", node_id=node.node_id)

    await asyncio.gather(*(one(n) for n in nodes))


async def _get_or_create_doc(session: AsyncSession, file_id: int) -> WikiDocument:
    doc = (
        await session.execute(
            select(WikiDocument).where(WikiDocument.file_id == file_id)
        )
    ).scalars().first()
    if doc is None:
        doc = WikiDocument(file_id=file_id, version=0, status="pending")
        session.add(doc)
        await session.flush()
    return doc


async def index_file(
    session: AsyncSession, storage: StorageService, file_id: int
) -> str:
    """파일 하나를 인덱싱한다. 반환: 최종 status.

    큐에 담긴 시점과 처리 시점 사이에 상태가 바뀌었을 수 있으므로 **여기서 다시 확인한다** —
    위키가 꺼졌거나 휴지통에 갔으면 아무 것도 하지 않는다.
    """
    file = await session.get(File, file_id)
    if file is None or file.is_deleted or file.is_folder:
        return "skipped"

    state = await wiki_service.resolve_wiki_state(session, file_id)
    if not state.enabled:
        return "skipped"
    verdict = wiki_service.indexable(file)
    if not verdict.ok:
        return "skipped"

    doc = await _get_or_create_doc(session, file_id)
    if doc.status == "ready" and doc.version == file.current_version:
        return "ready"  # 이미 최신 — 멱등

    target_version = file.current_version
    doc.status = "indexing"
    await session.commit()

    try:
        markdown = await _load_markdown(storage, file)
        roots, lines = wiki_tree.parse(markdown, doc_name=file.name)
        await _summarize(wiki_tree.flatten(roots), lines)
        tree = wiki_tree.to_dict(roots, doc_name=file.name, line_count=len(lines))
    except Exception as exc:  # noqa: BLE001 - 실패 사유를 남기고 구 트리를 유지한다
        await session.rollback()
        doc = await _get_or_create_doc(session, file_id)
        doc.status = "failed"
        doc.error = str(exc)[:500]
        await session.commit()
        log.exception("wiki_index_failed", file_id=file_id, name=file.name)
        return "failed"

    # 원자적 스왑 — 여기까지 와야 트리를 갈아끼운다.
    doc = await _get_or_create_doc(session, file_id)
    doc.tree = tree
    doc.version = target_version
    doc.status = "ready"
    doc.error = None
    doc.indexed_at = datetime.now(UTC)
    await session.commit()

    log.info(
        "wiki_indexed",
        file_id=file_id,
        name=file.name,
        version=target_version,
        nodes=len(wiki_tree.flatten(roots)),
    )
    await _publish(file)
    return "ready"


async def _publish(file: File) -> None:
    """인덱싱 완료를 열려 있는 화면에 알린다 (fail-open)."""
    from app.services import file_events as file_events_service

    try:
        await file_events_service.publish_file_event(
            type="wiki",
            file_id=file.id,
            parent_folder_id=file.parent_folder_id,
            actor_id=file.user_id,
            name=file.name,
        )
    except Exception:  # noqa: BLE001 - 통지 실패가 인덱싱을 되돌리지 않는다
        log.warning("wiki_event_publish_failed", file_id=file.id)


async def run_once(
    session: AsyncSession, storage: StorageService, limit: int = 5
) -> dict[str, int]:
    """큐에서 처리 시각이 된 작업을 집어 처리한다. 반환: status 별 개수."""
    file_ids = await wiki_queue.claim(limit)
    counts: dict[str, int] = {}
    for fid in file_ids:
        try:
            status = await index_file(session, storage, fid)
        except Exception:  # noqa: BLE001 - 한 건의 실패가 회차를 멈추지 않게 한다
            await session.rollback()
            status = "failed"
            log.exception("wiki_index_unhandled", file_id=fid)
        counts[status] = counts.get(status, 0) + 1
    return counts


__all__ = ["index_file", "run_once"]
