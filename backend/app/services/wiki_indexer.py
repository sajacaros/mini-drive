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

그리고 이 파이프라인 바깥에 **정합 스윕**(`reconcile`)이 있다. 위 훅들이 전부 fail-open 이라
예약이 조용히 새는 구간이 남는데, 스윕이 유효 상태를 재도출해 그 차이를 메운다. 자세한 것은
`reconcile` 의 docstring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings, settings
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


async def _summarize(
    nodes: list[wiki_tree.TreeNode],
    lines: list[str],
    *,
    cache: dict[str, str] | None = None,
) -> tuple[int, int]:
    """긴 절만 LLM 으로 요약하고, 짧은 절은 본문을 그대로 요약으로 둔다. 반환: (호출, 재사용).

    짧은 절을 요약하면 원문보다 길어지거나 정보가 준다. 검색에는 본문 자체가 더 낫다.

    `cache` 는 직전 트리의 `{text_hash: summary}` 다(`wiki_tree.summary_cache`). 재색인 비용의
    거의 전부가 이 함수의 LLM 호출이라, 절 하나를 고쳐 다시 올린 문서가 나머지 절을 통째로
    다시 요약하면 그만큼 GPU 를 문다 — 대화형 질의 지연이 그대로 커지는 자리다
    (spec/wiki-index.md 「GPU 경합」). 입력이 같으면 결과도 같다고 보고 건너뛴다.
    """
    sem = asyncio.Semaphore(_SUMMARY_CONCURRENCY)
    calls = 0
    hits = 0

    async def one(node: wiki_tree.TreeNode) -> None:
        nonlocal calls, hits
        body = wiki_tree.own_text(lines, node)
        if not body:
            return
        if len(body) < wiki_tree.SHORT_NODE_CHARS:
            node.summary = body
            return
        if cache:
            cached = cache.get(node.text_hash)
            if cached:
                node.summary = cached
                hits += 1
                return
        prompt = _SUMMARY_PROMPT.format(title=node.title, body=body)
        async with sem:
            calls += 1
            try:
                node.summary = await wiki_llm.complete(prompt)
            except wiki_llm.WikiLLMError:
                # 한 노드의 요약 실패가 트리 전체를 버리게 하지 않는다 — 본문 앞부분으로 대신한다.
                node.summary = body[: wiki_tree.SHORT_NODE_CHARS]
                # **이 대체물은 캐시에 남기지 않는다.** 해시를 지우면 트리에 열쇠가 안 실려
                # 다음 재색인이 이 절을 다시 요약한다. 남겨 두면 LLM 이 잠깐 죽은 회차의
                # 본문 앞부분 400자가 그 절의 요약으로 영구히 굳는다.
                node.text_hash = ""
                log.warning("wiki_summary_failed", node_id=node.node_id)

    await asyncio.gather(*(one(n) for n in nodes))
    return calls, hits


async def _get_or_create_doc(session: AsyncSession, file_id: int) -> WikiDocument:
    doc = (
        await session.execute(
            select(WikiDocument).where(WikiDocument.file_id == file_id)
        )
    ).scalars().first()
    if doc is None:
        doc = WikiDocument(
            file_id=file_id, version=0, status=wiki_service.WikiStatus.PENDING
        )
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
    if (
        doc.status == wiki_service.WikiStatus.READY
        and doc.version == file.current_version
    ):
        return "ready"  # 이미 최신 — 멱등

    target_version = file.current_version
    # 직전 트리의 요약을 재사용 후보로 들고 간다 — 스왑 전에 읽어야 한다.
    cache = wiki_tree.summary_cache(doc.tree)
    doc.status = wiki_service.WikiStatus.INDEXING
    await session.commit()

    try:
        markdown = await _load_markdown(storage, file)
        roots, lines = wiki_tree.parse(markdown, doc_name=file.name)
        calls, hits = await _summarize(wiki_tree.flatten(roots), lines, cache=cache)
        tree = wiki_tree.to_dict(roots, doc_name=file.name, line_count=len(lines))
    except Exception as exc:  # noqa: BLE001 - 실패 사유를 남기고 구 트리를 유지한다
        await session.rollback()
        doc = await _get_or_create_doc(session, file_id)
        doc.status = wiki_service.WikiStatus.FAILED
        doc.error = str(exc)[:500]
        # 연속 실패를 세어 스윕의 재시도에 상한을 준다(wiki_indexer.reconcile).
        doc.attempts += 1
        doc.last_attempt_at = datetime.now(UTC)
        await session.commit()
        log.exception("wiki_index_failed", file_id=file_id, name=file.name)
        return "failed"

    # 원자적 스왑 — 여기까지 와야 트리를 갈아끼운다.
    doc = await _get_or_create_doc(session, file_id)
    doc.tree = tree
    doc.version = target_version
    doc.status = wiki_service.WikiStatus.READY
    doc.error = None
    doc.indexed_at = datetime.now(UTC)
    doc.attempts = 0
    doc.last_attempt_at = doc.indexed_at
    await session.commit()

    log.info(
        "wiki_indexed",
        file_id=file_id,
        name=file.name,
        version=target_version,
        nodes=len(wiki_tree.flatten(roots)),
        summary_calls=calls,
        summary_reused=hits,
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


@dataclass(frozen=True)
class ReconcileResult:
    """정합 스윕 한 회차의 결과. 그대로 로그 한 줄이 된다."""

    candidates: int = 0  # 유효 ON 이고 인덱싱 대상인 파일 수
    missing: int = 0  # 켜져 있는데 행이 아예 없던 것
    revived: int = 0  # disabled 로 남아 있었는데 다시 켜져 있는 것
    outdated: int = 0  # ready 인데 트리가 현재 버전보다 낡은 것
    stuck: int = 0  # pending/indexing/stale 인데 큐에서 사라진 것
    retried: int = 0  # failed 재시도
    exhausted: int = 0  # 재시도 상한/백오프에 걸려 이번 회차에 건너뛴 것
    disabled: int = 0  # 질의 대상이었지만 유효 OFF 라 내린 것
    disable_skipped: int = 0  # 대량 내리기 가드에 걸려 보류한 것
    enqueued: int = 0
    dry_run: bool = False


# 한 회차에 내릴 수 있는 문서 비율의 상한. 넘으면 **내리지 않고 로그만 남긴다.**
#
# 이 스윕은 유효 상태를 재도출해 저장 상태를 덮어쓰므로, 도출 질의가 잘못되면(스키마 변경,
# 조인 실수) 멀쩡한 문서를 통째로 질의 대상에서 빼는 일이 한 회차 만에 벌어진다. 정상 운영에서
# 한 번에 절반이 꺼지는 일은 없고, 실제로 그런 조작이 있었다면 즉시 훅이 이미 처리했을 것이다 —
# 스윕까지 온 대량 내리기는 훅이 통째로 실패했거나 우리가 틀렸다는 뜻이라, 어느 쪽이든 사람이
# 봐야 한다.
#
# 바닥값을 두는 이유는 작은 배포다. 문서가 4건인 곳에서는 2건만 꺼도 50%라 정상 조작이 늘
# 가드에 걸린다.
_DISABLE_GUARD_RATIO = 0.5
_DISABLE_GUARD_FLOOR = 20

# 한 번에 처리할 파일 수. 스윕은 전수를 훑으므로 갱신·큐 투입을 나눠 넣는다 —
# 수천 건짜리 IN 절 하나와 긴 트랜잭션을 만들지 않기 위한 것이다.
_RECONCILE_BATCH = 500


def _chunks(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass(frozen=True)
class StoredDoc:
    """`wiki_documents` 한 행에서 정합 판정에 필요한 값만. 트리는 읽지 않는다."""

    file_id: int
    status: str
    version: int
    attempts: int
    last_attempt_at: datetime | None


@dataclass(frozen=True)
class ReconcilePlan:
    """스윕이 무엇을 할지. **DB 를 건드리기 전에** 전부 정해진다."""

    to_pending: list[int]  # 행 생성·부활 (mark_pending 경유)
    to_stale: list[int]  # 낡음 표시 후 재색인
    to_requeue: list[int]  # 상태는 그대로 두고 큐에만 다시 넣는다
    to_disable: list[int]
    missing: int = 0
    revived: int = 0
    outdated: int = 0
    stuck: int = 0
    retried: int = 0
    exhausted: int = 0
    disable_skipped: int = 0


def plan_reconcile(
    *,
    targets: dict[int, int],
    stored: list[StoredDoc],
    now: datetime,
    max_attempts: int,
    retry_hours: int,
) -> ReconcilePlan:
    """정합 판정 — 순수 함수. `targets` 는 `{file_id: 현재 버전}`(유효 ON + 색인 대상).

    IO 와 갈라 둔 이유는 검증이다. 이 함수가 스윕의 전부이면서 DB·Redis 를 모르므로, 다섯
    가지 어긋남과 가드를 단위 테스트로 고정할 수 있다 — 스윕이 잘못 판정하면 멀쩡한 문서를
    매일 되돌리거나 통째로 내리는 쪽이라, 통합 환경이 있어야만 검증되는 자리에 두지 않는다.
    """
    to_pending: list[int] = []
    to_stale: list[int] = []
    to_requeue: list[int] = []
    to_disable: list[int] = []
    missing = revived = outdated = stuck = retried = exhausted = 0

    by_file = {doc.file_id: doc for doc in stored}

    for file_id, current_version in targets.items():
        doc = by_file.get(file_id)
        if doc is None:
            missing += 1
            to_pending.append(file_id)
            continue

        behind = doc.version < current_version

        if doc.status == wiki_service.WikiStatus.DISABLED:
            # 다시 켜져 있다. 트리와 버전이 그대로면 mark_pending 이 비용 0 으로 ready 로
            # 되돌리고, 아니면 pending 으로 올려 재색인한다.
            revived += 1
            to_pending.append(file_id)
        elif doc.status == wiki_service.WikiStatus.READY:
            if behind:
                outdated += 1
                to_stale.append(file_id)
        elif doc.status == wiki_service.WikiStatus.FAILED:
            # 상태는 `failed` 로 둔 채 큐에만 넣는다. `stale` 로 올리면 트리가 없는 실패 행이
            # "트리는 있으나 낡음"이라고 말하게 되고, 카탈로그가 거짓을 표시한다.
            if _retry_due(
                attempts=doc.attempts,
                last_attempt_at=doc.last_attempt_at,
                behind=behind,
                now=now,
                max_attempts=max_attempts,
                retry_hours=retry_hours,
            ):
                retried += 1
                to_requeue.append(file_id)
            else:
                exhausted += 1
        else:  # pending | indexing | stale — 큐에서 사라진 흔적
            stuck += 1
            to_requeue.append(file_id)

    # ── 반대 방향: 대상이 아닌데 질의에 잡히는 행 ──────────────────────
    for doc in stored:
        if doc.file_id not in targets and doc.status != wiki_service.WikiStatus.DISABLED:
            to_disable.append(doc.file_id)

    disable_skipped = 0
    if to_disable and _disable_guard_tripped(len(to_disable), len(stored)):
        disable_skipped = len(to_disable)
        to_disable = []

    return ReconcilePlan(
        to_pending=to_pending,
        to_stale=to_stale,
        to_requeue=to_requeue,
        to_disable=to_disable,
        missing=missing,
        revived=revived,
        outdated=outdated,
        stuck=stuck,
        retried=retried,
        exhausted=exhausted,
        disable_skipped=disable_skipped,
    )


async def reconcile(session: AsyncSession, *, dry_run: bool = False) -> ReconcileResult:
    """저장된 색인 상태를 **재도출한 유효 상태에 맞춘다**. 반환: 회차 결과.

    위키의 정합 훅은 전부 fail-open 이다 — 업로드·이동·토글이 인덱싱 예약 실패로 되돌아가면
    안 되기 때문이고(`files.sync_wiki`, `wiki_queue.enqueue`), 그 대가로 예약이 조용히 새는
    구간이 생긴다. spec 이 곳곳에서 "놓친 항목은 다음 토글·버전업에서 다시 들어온다"고 적어
    둔 것이 그 대가를 사용자 조작에 떠넘긴 것인데, **한 번 올리고 안 건드리는 문서에는 그 다음이
    오지 않는다.** 여기가 그 빚을 갚는 자리다.

    바로잡는 어긋남이 다섯 가지다.

    ① `ready` 인데 트리가 현재 버전보다 낡음 — 가장 나쁜 상태다. 카탈로그가 "최신"이라고
       말하는데 내용은 몇 버전 전이라 사용자가 그것을 알 방법이 없다.
    ② 켜져 있는데 행이 아예 없음 — 카탈로그에도 큐에도 없어 관측 자체가 불가능하다.
    ③ `pending`/`indexing`/`stale` 인데 큐에서 사라짐 — Redis flush·워커 크래시의 흔적이다.
    ④ `failed` 로 굳음 — LLM 일시 장애 한 번이 영구 실패가 되던 자리다(재시도 경로가 없었다).
    ⑤ 유효 OFF·대상 밖인데 질의 대상으로 남음 — 불변식(인덱싱 ⟺ `@전사 read`)의 반대 방향이라
       회수된 문서로 계속 답하게 된다.

    **인덱싱을 여기서 돌리지는 않는다.** 어긋난 것을 큐에 넣을 뿐이고 실제 색인은 워커가
    자기 속도로 집어 간다 — 스윕이 직접 LLM 을 태우면 하루 한 번 GPU 를 통째로 물게 된다.

    멱등하다. 아무것도 어긋나지 않았으면 질의 두 번으로 끝나고 아무 것도 쓰지 않는다.

    dry_run : 판정만 하고 아무 것도 쓰지 않는다. `purge-trash --dry-run` 과 같은 결로,
              "이 스윕이 지금 무엇을 건드리려 하는가"를 먼저 보기 위한 것이다 — 상태를 되돌리는
              작업이라 처음 붙이는 배포에서는 눈으로 확인하고 돌리는 편이 낫다.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    # ── 유효 상태 재도출 ────────────────────────────────────────────────
    enabled = await wiki_service.enabled_file_rows(session)
    targets = {
        row.file_id: row
        for row in enabled
        if wiki_service.indexable_row(name=row.name, size=row.size).ok
    }

    # ── 저장 상태 ──────────────────────────────────────────────────────
    # 트리(JSONB)는 읽지 않는다 — 수백 건의 본문 크기 컬럼을 회차마다 끌어올 이유가 없다.
    stored = (
        await session.execute(
            select(
                WikiDocument.file_id,
                WikiDocument.status,
                WikiDocument.version,
                WikiDocument.attempts,
                WikiDocument.last_attempt_at,
            )
        )
    ).all()

    plan = plan_reconcile(
        targets={fid: row.current_version for fid, row in targets.items()},
        stored=[
            StoredDoc(
                file_id=r.file_id,
                status=r.status,
                version=r.version,
                attempts=r.attempts,
                last_attempt_at=r.last_attempt_at,
            )
            for r in stored
        ],
        now=now,
        max_attempts=settings.wiki_reconcile_max_attempts,
        retry_hours=settings.wiki_reconcile_retry_hours,
    )

    if plan.disable_skipped:
        log.error(
            "wiki_reconcile_disable_guard",
            would_disable=plan.disable_skipped,
            documents=len(stored),
            hint="유효 상태 도출이 잘못됐을 수 있다 — 확인 전까지 내리지 않는다.",
        )

    # ── 적용 ───────────────────────────────────────────────────────────
    enqueue_ids = plan.to_pending + plan.to_stale + plan.to_requeue
    if not dry_run:
        for chunk in _chunks(plan.to_pending, _RECONCILE_BATCH):
            await wiki_service.mark_pending(session, chunk)
        for chunk in _chunks(plan.to_stale, _RECONCILE_BATCH):
            await session.execute(
                update(WikiDocument)
                .where(WikiDocument.file_id.in_(chunk))
                .values(status=wiki_service.WikiStatus.STALE)
            )
            await session.commit()
        for chunk in _chunks(plan.to_disable, _RECONCILE_BATCH):
            await wiki_service.mark_disabled(session, chunk)
            await wiki_queue.drop(chunk)
        for chunk in _chunks(enqueue_ids, _RECONCILE_BATCH):
            await wiki_queue.enqueue(chunk, delay=0)

    return ReconcileResult(
        dry_run=dry_run,
        candidates=len(targets),
        missing=plan.missing,
        revived=plan.revived,
        outdated=plan.outdated,
        stuck=plan.stuck,
        retried=plan.retried,
        exhausted=plan.exhausted,
        disabled=len(plan.to_disable),
        disable_skipped=plan.disable_skipped,
        enqueued=len(enqueue_ids),
    )


def _retry_due(
    *,
    attempts: int,
    last_attempt_at: datetime | None,
    behind: bool,
    now: datetime,
    max_attempts: int,
    retry_hours: int,
) -> bool:
    """`failed` 로 남은 문서를 이번 회차에 다시 태울 것인가.

    새 버전이 올라왔으면 **입력이 바뀐 것**이므로 시도 횟수를 따지지 않는다 — 지난번에 죽은
    이유가 그 파일에 있었다면 새 파일에서는 다를 수 있고, 그걸 안 돌리면 사용자가 고쳐 올려도
    영영 실패로 남는다(`mark_pending` 이 카운터를 0 으로 되돌리는 것도 같은 취지다).

    그 외에는 상한과 간격을 둘 다 본다. 상한만 두면 스윕을 손으로 몇 번 돌리는 것만으로
    시도가 소진되고, 간격만 두면 상한 초과 파일처럼 영구 실패가 확정된 문서를 매일 굽는다.
    """
    if behind:
        return True
    if attempts >= max_attempts:
        return False
    if last_attempt_at is None:
        return True
    return now - last_attempt_at >= timedelta(hours=retry_hours)


def _disable_guard_tripped(count: int, total: int) -> bool:
    """한 회차의 내리기가 과한가 (`_DISABLE_GUARD_RATIO` 주석 참조)."""
    if count <= _DISABLE_GUARD_FLOOR:
        return False
    return total > 0 and count > total * _DISABLE_GUARD_RATIO


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


__all__ = [
    "ReconcilePlan",
    "ReconcileResult",
    "StoredDoc",
    "index_file",
    "plan_reconcile",
    "reconcile",
    "run_once",
]
