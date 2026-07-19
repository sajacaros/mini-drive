"""전사 단일 위키 서비스 (LLM 위키, wiki-v2 재설계, Phase 7-4).

위키 v2 재설계로 스페이스(personal/group 스코프) 개념을 제거했다. 새 모델:

  - 드라이브 파일/폴더의 **"위키에 공유" 체크**(= wiki_sources 행) = 소유자의 명시적 **출판**.
    체크된 콘텐츠는 **전사 단일 위키**로 컴파일되고, 컴파일된 위키 페이지는 **모든 로그인
    사용자**가 읽는다(권한 서비스 특례, wiki-v2 D4). 원본은 기존 권한 그대로 보호된다 —
    위키 페이지 출처 링크는 클릭 시점에 다시 ensure_file_access 를 통과한다(비권한자 403).
  - 위키 페이지는 별도 테이블이 아니라 **시스템 사용자 소유** `Wiki` 폴더(부트스트랩,
    app_settings 의 wiki_root_folder_id) 하위의 일반 드라이브 파일이다. 시스템 사용자는 로그인
    불가(status=inactive)하며 백그라운드 컴파일이 페이지를 쓸 때의 자격이다(wiki-v2 D3).

불변식은 "권한 경계 = 컴파일 경계"에서 "**출판 동의(체크) = 전사 출판**"으로 대체됐다(wiki-v2
D1). 이 서비스는 부트스트랩·소스 수명주기·컴파일 오케스트레이션·챗 범위 산출을 담당한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import arq_queue
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import (
    AppSetting,
    File,
    User,
    WikiJob,
    WikiSource,
)
from app.models.enums import UserRole, UserStatus
from app.models.wiki import (
    JOB_DONE,
    JOB_FAILED,
    JOB_INGEST,
    JOB_QUEUED,
    JOB_RUNNING,
    SOURCE_FAILED,
    SOURCE_INDEXED,
    SOURCE_QUEUED,
    SOURCE_STALE,
)
from app.services import files as files_service
from app.services import indexing as indexing_service
from app.services import permissions as permissions_service
from app.services import wiki_compile
from app.services.chat_llm import ChatProvider
from app.services.permissions import (
    WIKI_ROOT_FOLDER_KEY,
    WIKI_SYSTEM_USER_KEY,
)
from app.services.storage import StorageService

_log = get_logger("app.wiki")

# 상세 조회에 포함할 최근 log.md 항목 수.
_RECENT_LOG_LIMIT = 20

# 초기 카탈로그/로그 스텁.
_INDEX_STUB = "# 위키 카탈로그\n\n(아직 페이지가 없습니다. 소스를 등록하면 컴파일됩니다.)\n"
_LOG_STUB = "# 컴파일 로그\n"

# 위키 시스템 사용자(로그인 불가) 규약(wiki-v2 D3).
WIKI_SYSTEM_EMAIL = "wiki-system@internal.invalid"
WIKI_SYSTEM_NAME = "Wiki System"
WIKI_FOLDER_NAME = "Wiki"
# 시스템 사용자 quota — 위키 페이지 누적을 사실상 제한하지 않는다(대용량, wiki-v2 D3).
WIKI_SYSTEM_MAX_STORAGE = 1 << 50  # 1 PiB
# 동시 부트스트랩 레이스 방어용 sentinel(app_settings PK ON CONFLICT DO NOTHING, setup 패턴).
_WIKI_BOOTSTRAP_LOCK = "wiki_bootstrap_lock"


class WikiServiceError(Exception):
    """위키 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# 대상 파일과 모든 하위 항목을 재귀로 훑는 공통 CTE (files.py/indexing.py 와 동일 패턴).
_SUBTREE_CTE = (
    "WITH RECURSIVE sub AS ("
    "  SELECT id FROM files WHERE id = :root "
    "  UNION ALL "
    "  SELECT f.id FROM files f JOIN sub s ON f.parent_folder_id = s.id"
    ")"
)


# --- 부트스트랩 (lazy, 멱등, wiki-v2 3장) ------------------------------------


async def _get_setting_int(session: AsyncSession, key: str) -> int | None:
    row = await session.get(AppSetting, key)
    return int(row.value) if row is not None else None


async def bootstrap_wiki(
    session: AsyncSession, storage: StorageService
) -> tuple[int, int]:
    """전사 위키를 부트스트랩한다(멱등). (wiki_root_folder_id, wiki_system_user_id) 반환.

    첫 공유 체크(또는 첫 위키 조회) 시 시스템 사용자(로그인 불가) → 그 root 하위 `Wiki` 폴더 →
    index.md/log.md 스텁 → app_settings 기록. 동시 부트스트랩 레이스는 sentinel 키의
    ON CONFLICT DO NOTHING 으로 방어한다 — 먼저 잠근 요청만 실제 생성하고, 나머지는 잠금 해제
    (winner 커밋) 후 기록된 id 를 읽는다(setup 서비스와 동일 패턴, wiki-v2 3장).
    """
    root_id = await _get_setting_int(session, WIKI_ROOT_FOLDER_KEY)
    sys_id = await _get_setting_int(session, WIKI_SYSTEM_USER_KEY)
    if root_id is not None and sys_id is not None:
        permissions_service.set_wiki_root_folder_id(root_id)
        return root_id, sys_id

    # sentinel 원자적 선점 — winner 만 실제 생성한다. loser 는 winner 커밋까지 블록된 뒤 no-row.
    claimed = await session.execute(
        pg_insert(AppSetting)
        .values(key=_WIKI_BOOTSTRAP_LOCK, value=True)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(AppSetting.key)
    )
    if claimed.first() is None:
        # 다른 요청이 먼저 부트스트랩했다(그 트랜잭션이 방금 커밋됨) — 기록된 id 를 읽는다.
        await session.rollback()
        root_id = await _get_setting_int(session, WIKI_ROOT_FOLDER_KEY)
        sys_id = await _get_setting_int(session, WIKI_SYSTEM_USER_KEY)
        if root_id is None or sys_id is None:  # pragma: no cover - winner 롤백 등 예외 경로
            raise WikiServiceError(500, "위키 부트스트랩에 실패했습니다.")
        permissions_service.set_wiki_root_folder_id(root_id)
        return root_id, sys_id

    # winner — 시스템 사용자 + Wiki 폴더 + 설정을 원자적으로 만든다(스텁 페이지는 커밋 후).
    await session.execute(
        pg_insert(User)
        .values(
            email=WIKI_SYSTEM_EMAIL,
            password_hash="!",  # 로그인 불가(유효 해시 아님) + status=inactive 로 이중 차단.
            display_name=WIKI_SYSTEM_NAME,
            role=UserRole.USER,
            status=UserStatus.INACTIVE,
            max_storage=WIKI_SYSTEM_MAX_STORAGE,
        )
        .on_conflict_do_nothing(index_elements=["email"])
    )
    sys_user = (
        await session.execute(select(User).where(User.email == WIKI_SYSTEM_EMAIL))
    ).scalar_one()

    sys_user_id = sys_user.id

    from app.services.users import create_root_folder

    root = await create_root_folder(session, sys_user)  # 멱등 — 기존 있으면 재사용.
    wiki_folder = await files_service.find_active_child(
        session, root.id, WIKI_FOLDER_NAME
    )
    if wiki_folder is None:
        wiki_folder = File(
            user_id=sys_user_id,
            group_id=None,
            parent_folder_id=root.id,
            name=WIKI_FOLDER_NAME,
            file_key="",
            mime_type=None,
            size=0,
            is_folder=True,
        )
        session.add(wiki_folder)
        await session.flush()
    # 커밋 후엔 ORM 속성이 만료돼 async lazy-load 가 되므로 id 를 미리 캡처한다.
    root_folder_id = wiki_folder.id

    await _set_setting(session, WIKI_ROOT_FOLDER_KEY, root_folder_id)
    await _set_setting(session, WIKI_SYSTEM_USER_KEY, sys_user_id)
    await session.commit()
    permissions_service.set_wiki_root_folder_id(root_folder_id)

    # 스텁 index.md/log.md(없을 때만) — write_text_file_as 는 내부 커밋하므로 설정 커밋 뒤 쓴다.
    sys_user = await session.get(User, sys_user_id)
    if await files_service.find_active_child(
        session, root_folder_id, wiki_compile.INDEX_PAGE
    ) is None:
        await files_service.write_text_file_as(
            session, storage, sys_user,
            parent_id=root_folder_id, name=wiki_compile.INDEX_PAGE,
            content=_INDEX_STUB.encode(),
        )
    if await files_service.find_active_child(
        session, root_folder_id, wiki_compile.LOG_PAGE
    ) is None:
        await files_service.write_text_file_as(
            session, storage, sys_user,
            parent_id=root_folder_id, name=wiki_compile.LOG_PAGE,
            content=_LOG_STUB.encode(),
        )
    _log.info(
        "wiki_bootstrapped", root_folder_id=root_folder_id, system_user_id=sys_user.id
    )
    return root_folder_id, sys_user.id


async def _set_setting(session: AsyncSession, key: str, value: object) -> None:
    """app_settings 를 upsert 한다(커밋은 호출자). setup 서비스의 set_setting 과 동일 패턴."""
    from sqlalchemy import func

    await session.execute(
        pg_insert(AppSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=["key"], set_={"value": value, "updated_at": func.now()}
        )
    )


async def _ensure_wiki(
    session: AsyncSession, storage: StorageService
) -> tuple[int, User]:
    """위키를 보장(부트스트랩)하고 (root_folder_id, 시스템 사용자) 를 반환한다.

    페이지 쓰기 owner = 시스템 사용자(wiki-v2 D3). 모든 위키 서비스 작업의 진입 헬퍼.
    """
    root_id, sys_id = await bootstrap_wiki(session, storage)
    sys_user = await session.get(User, sys_id)
    if sys_user is None:  # pragma: no cover - 부트스트랩 직후엔 항상 존재
        raise WikiServiceError(500, "위키 시스템 사용자를 찾을 수 없습니다.")
    return root_id, sys_user


# --- 잡 기록 ----------------------------------------------------------------


async def record_job(
    session: AsyncSession,
    kind: str,
    *,
    file_id: int | None = None,
    status: str = JOB_QUEUED,
    error: str | None = None,
) -> WikiJob:
    """wiki_jobs 이력 한 건을 기록한다(커밋은 호출자 조율). 상태 조회·재시도 판단의 진실 소스."""
    job = WikiJob(file_id=file_id, kind=kind, status=status, error=error)
    session.add(job)
    return job


# --- 소스 등록 / 제거 (wiki-v2 4.2/4.4) -------------------------------------


@dataclass(frozen=True)
class SourceInfo:
    file_id: int
    file_name: str
    status: str
    last_ingested_version: int | None
    added_by: int


async def register_source(
    session: AsyncSession,
    storage: StorageService,
    actor: User,
    *,
    file_id: int,
) -> WikiSource:
    """"위키에 공유" 체크 — 소스를 등록하고 Ingest 잡을 큐잉한다(wiki-v2 4.4, D1).

    **항목 소유자만** 체크할 수 있다(소유자 아니면 403, admin 도 불가 — D1). 파일/폴더 무관.
    성공 시 wiki_sources upsert(queued) + wiki_jobs(ingest) + arq wiki_ingest(file_id) 큐잉.
    """
    await bootstrap_wiki(session, storage)

    file = await files_service.get_file(session, file_id)
    if file is None or file.is_deleted:
        raise WikiServiceError(404, "소스 파일을 찾을 수 없습니다.")
    # 출판 동의 = 소유자만(wiki-v2 D1). admin 도 타인 콘텐츠를 대신 출판할 수 없다.
    if file.user_id != actor.id:
        raise WikiServiceError(403, "위키 공유는 항목 소유자만 설정할 수 있습니다.")

    existing = await session.get(WikiSource, file_id)
    if existing is None:
        source = WikiSource(
            file_id=file_id,
            status=SOURCE_QUEUED,
            added_by=actor.id,
        )
        session.add(source)
    else:
        existing.status = SOURCE_QUEUED
        source = existing

    await record_job(session, JOB_INGEST, file_id=file_id)
    await session.commit()
    await session.refresh(source)

    # arq 큐잉(실패는 경고만 — fail-open). 워커가 컴파일을 수행한다.
    await arq_queue.enqueue_wiki_ingest(file_id)
    _log.info("wiki_source_registered", file_id=file_id, added_by=actor.id)
    return source


async def remove_source(
    session: AsyncSession,
    storage: StorageService,
    actor: User,
    file_id: int,
) -> None:
    """위키 공유 해제 — 소스를 제거한다(wiki-v2 4.4, D5). **소유자만**.

    페이지 자동 삭제는 하지 않고(컴파일된 지식은 자산, D5) log.md 에 기록한다(Lint 로 정리 안내).
    소스 파일의 청크는 지우지 않는다(파일 자체는 드라이브에 남아 다른 경로로 검색될 수 있음).
    """
    root_id, owner = await _ensure_wiki(session, storage)

    existing = await session.get(WikiSource, file_id)
    if existing is None:
        raise WikiServiceError(404, "등록된 소스가 아닙니다.")
    if existing.added_by != actor.id:
        raise WikiServiceError(403, "위키 공유 해제는 소유자만 할 수 있습니다.")
    await session.delete(existing)
    await session.commit()

    # log.md 에 제거 기록(관련 페이지 stale 안내는 Lint 리포트가 담당).
    _, log_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.LOG_PAGE
    )
    entry = wiki_compile.log_entry("source-removed", f"file:{file_id}", [])
    new_log = wiki_compile.append_log(log_md, entry)
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=root_id, name=wiki_compile.LOG_PAGE, content=new_log.encode(),
    )
    _log.info("wiki_source_removed", file_id=file_id)


# --- 개요 / 잡 이력 (wiki-v2 4.2) -------------------------------------------


@dataclass(frozen=True)
class WikiOverview:
    root_folder_id: int
    sources: list[SourceInfo]
    recent_log: list[str]
    index_entries: list[str]


async def get_overview(
    session: AsyncSession, storage: StorageService, user: User
) -> WikiOverview:
    """위키 개요 — 카탈로그(index_entries), 최근 log.md 항목, 공유 소스 현황(wiki-v2 4.2).

    로그인 사용자 누구나 조회한다(전사 단일 위키).
    """
    root_id, _ = await _ensure_wiki(session, storage)

    src_rows = (
        await session.execute(
            select(WikiSource, File.name)
            .join(File, File.id == WikiSource.file_id)
            .order_by(WikiSource.created_at.asc())
        )
    ).all()
    sources = [
        SourceInfo(
            file_id=s.file_id,
            file_name=name,
            status=s.status,
            last_ingested_version=s.last_ingested_version,
            added_by=s.added_by,
        )
        for s, name in src_rows
    ]

    _, index_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.INDEX_PAGE
    )
    _, log_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.LOG_PAGE
    )
    return WikiOverview(
        root_folder_id=root_id,
        sources=sources,
        recent_log=wiki_compile.recent_log_entries(log_md, _RECENT_LOG_LIMIT),
        index_entries=wiki_compile.index_entries(index_md),
    )


async def list_jobs(
    session: AsyncSession, user: User, limit: int = 50
) -> list[WikiJob]:
    """위키 잡 이력(최근순). 로그인 사용자 누구나(전사 자산, wiki-v2 4.2)."""
    rows = (
        await session.execute(
            select(WikiJob).order_by(WikiJob.id.desc()).limit(limit)
        )
    ).scalars().all()
    return list(rows)


# --- 챗 범위 파일 집합 (wiki-v2 4.5) -----------------------------------------


async def _subtree_ids(session: AsyncSession, root_id: int) -> list[int]:
    rows = await session.execute(
        text(_SUBTREE_CTE + " SELECT id FROM sub"), {"root": root_id}
    )
    return [r.id for r in rows]


@dataclass(frozen=True)
class WikiScope:
    """챗 위키 범위 파일 집합. wiki_page_ids 는 우선순위 배치에 쓴다."""

    all_ids: set[int]
    wiki_page_ids: set[int]


async def _owned_descendant_files(
    session: AsyncSession, root_id: int, owner_id: int
) -> list[int]:
    """폴더 하위(재귀)의 비폴더·미삭제 파일 중 owner_id 소유 파일 id(폴더 소스 컴파일 범위, D2)."""
    ids = await indexing_service.eligible_descendant_files(session, root_id)
    if not ids:
        return []
    rows = (
        await session.execute(
            select(File.id).where(File.id.in_(ids), File.user_id == owner_id)
        )
    ).scalars().all()
    return list(rows)


async def wiki_file_scope(session: AsyncSession) -> WikiScope:
    """전사 위키 범위 파일 집합 = 위키 루트 서브트리(페이지) ∪ 등록 소스별 added_by 소유 파일.

    챗 세션이 위키 범위(wiki_scope)면 검색 후보를 이 집합으로 교집합한다(wiki-v2 4.5). 부트스트랩
    전이면 위키 페이지 집합은 비어 있다(설정만 조회, 강제 부트스트랩하지 않음).
    """
    root_id = await _get_setting_int(session, WIKI_ROOT_FOLDER_KEY)
    wiki_ids: set[int] = (
        set(await _subtree_ids(session, root_id)) if root_id is not None else set()
    )
    all_ids = set(wiki_ids)

    sources = (
        await session.execute(select(WikiSource.file_id, WikiSource.added_by))
    ).all()
    for file_id, added_by in sources:
        file = await files_service.get_file(session, file_id)
        if file is None or file.is_deleted:
            continue
        if file.is_folder:
            all_ids.update(
                await _owned_descendant_files(session, file_id, added_by)
            )
        else:
            all_ids.add(file_id)
    return WikiScope(all_ids=all_ids, wiki_page_ids=wiki_ids)


# --- Ingest 오케스트레이션 (워커 태스크가 호출, wiki-v2 4.4) -----------------


@dataclass(frozen=True)
class IngestSummary:
    """Ingest 잡 결과 요약. compiled 는 페이지가 생성/갱신된, failed 는 컴파일 실패한 소스 파일 수."""

    status: str
    compiled: int
    targets: int
    failed: int = 0


async def ingest_source(
    session: AsyncSession,
    storage: StorageService,
    chat_provider: ChatProvider,
    settings: Settings,
    file_id: int,
) -> IngestSummary:
    """소스 하나(파일 또는 폴더)를 컴파일한다 — 워커 wiki_ingest 태스크의 본체(wiki-v2 4.4).

    폴더면 하위 파일 중 **added_by 소유 파일만** 대상으로 팬아웃해 순차 컴파일한다(항상 재귀,
    타인 파일 제외 — D2). 파일당 1 페이지 컴파일. 페이지 owner = 시스템 사용자. 잡·소스 상태를
    진실 소스로 갱신하고, 실패는 wiki_jobs failed+error 로 남긴 뒤 예외를 다시 던져 arq 재시도에
    맡긴다.
    """
    root_id, owner = await _ensure_wiki(session, storage)
    source = await session.get(WikiSource, file_id)
    if source is None:
        return IngestSummary("skipped", 0, 0)  # 소스가 사라짐(경합)

    job = await record_job(
        session, JOB_INGEST, file_id=file_id, status=JOB_RUNNING
    )
    await session.commit()
    job_id = job.id

    try:
        file = await files_service.get_file(session, file_id)
        if file is None or file.is_deleted:
            raise WikiServiceError(404, "소스 파일을 찾을 수 없습니다.")
        # 컴파일 중 내부 커밋으로 file 이 만료되므로 버전을 미리 캡처한다(async lazy-load 회피).
        source_version = file.current_version
        added_by = source.added_by

        if file.is_folder:
            target_ids = await _owned_descendant_files(session, file_id, added_by)
        else:
            target_ids = [file_id]

        compiled = 0
        failed_names: list[str] = []
        for tid in target_ids:
            tfile = await files_service.get_file(session, tid)
            if tfile is None or tfile.is_deleted or tfile.is_folder:
                continue
            # rollback 시 ORM 객체가 만료되므로 이름을 미리 캡처한다(async lazy-load 회피).
            tname = tfile.name
            try:
                outcome = await wiki_compile.compile_source(
                    session,
                    storage,
                    chat_provider,
                    settings,
                    root_folder_id=root_id,
                    owner=owner,
                    source_file=tfile,
                )
            except Exception as exc:  # noqa: BLE001
                # 파일 하나의 실패(추출 불가·LLM 오류)가 폴더 전체 팬아웃을 막지 않게
                # 한다(fail-soft). 폴더 재시도는 성공분까지 재컴파일하므로 여기서 삼킨다.
                await session.rollback()
                failed_names.append(tname)
                _log.warning(
                    "wiki_compile_failed", file_id=tid, name=tname, error=str(exc)
                )
                continue
            if outcome.status == "compiled":
                compiled += 1
    except Exception as exc:  # noqa: BLE001 - 실패를 기록하고 arq 재시도에 맡긴다.
        await session.rollback()
        src = await session.get(WikiSource, file_id)
        if src is not None:
            src.status = SOURCE_FAILED
        failed_job = await session.get(WikiJob, job_id)
        if failed_job is not None:
            failed_job.status = JOB_FAILED
            failed_job.error = str(exc)[:2000]
        await session.commit()
        _log.warning("wiki_ingest_failed", file_id=file_id, error=str(exc))
        raise

    # 종료 — 소스/잡 상태 갱신(내부 컴파일 커밋으로 만료됐을 수 있어 재조회).
    # 일부 파일만 실패하면 done + error 메모(부분 성공), 전부 실패하면 failed 로 남긴다.
    # 어느 쪽도 예외를 던지지 않는다 — 폴더 단위 arq 재시도는 성공분까지 재컴파일하므로.
    all_failed = bool(failed_names) and compiled == 0
    error_note: str | None = None
    if failed_names:
        shown = ", ".join(failed_names[:5])
        more = f" 외 {len(failed_names) - 5}건" if len(failed_names) > 5 else ""
        error_note = f"{len(failed_names)}개 파일 컴파일 실패(추출/LLM 오류): {shown}{more}"
    src = await session.get(WikiSource, file_id)
    if src is not None:
        src.status = SOURCE_FAILED if all_failed else SOURCE_INDEXED
        if not all_failed:
            src.last_ingested_version = source_version
    done_job = await session.get(WikiJob, job_id)
    if done_job is not None:
        done_job.status = JOB_FAILED if all_failed else JOB_DONE
        done_job.error = error_note
    await session.commit()
    _log.info(
        "wiki_ingest_done",
        file_id=file_id,
        targets=len(target_ids),
        compiled=compiled,
        failed=len(failed_names),
    )
    status = "failed" if all_failed else "done"
    return IngestSummary(status, compiled, len(target_ids), len(failed_names))


# --- 답변 승격 (wiki-v2 4.2, D7) --------------------------------------------


async def promote_answer(
    session: AsyncSession,
    storage: StorageService,
    actor: User,
    *,
    message_id: int,
    title: str,
    content: str,
    citations: list[dict] | None,
) -> File:
    """챗 답변을 위키 페이지로 승격한다(wiki-v2 D7). 로그인 사용자 누구나(전사 출판 동의 행위).

    frontmatter 에 `promoted_from`(원본 메시지 id)·`locked: false` 를 붙이고 답변 본문 + 출처
    인용을 담아 페이지를 쓴다(index.md/log.md 부기 포함). 페이지 owner = 시스템 사용자. 인용
    링크는 클릭 시점에 다시 ensure_file_access 를 통과하므로 승격이 원본 권한을 넓히지 않는다.
    """
    title = title.strip()
    if not title:
        raise WikiServiceError(422, "페이지 제목이 비어 있습니다.")
    root_id, owner = await _ensure_wiki(session, storage)

    filename = wiki_compile.page_filename(title)
    existing, existing_md = await wiki_compile._read_page_text(
        session, storage, root_id, filename
    )
    if existing is not None and wiki_compile.is_locked(existing_md):
        raise WikiServiceError(409, "이미 잠긴(locked) 페이지가 있어 덮어쓸 수 없습니다.")

    cited_names = [
        str(c.get("file_name"))
        for c in (citations or [])
        if c.get("file_name")
    ]
    meta: dict[str, object] = {
        "title": title,
        "promoted_from": message_id,
        "locked": False,
        "sources": cited_names,
    }
    body_lines = [f"# {title}", "", content.strip()]
    if cited_names:
        body_lines += ["", "## 출처", *[f"- [{n}]" for n in dict.fromkeys(cited_names)]]
    page_md = wiki_compile.render_page(meta, "\n".join(body_lines))

    page = await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=root_id, name=filename, content=page_md.encode(),
    )

    # 부기 — index.md 갱신 + log.md append(승격).
    _, index_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.INDEX_PAGE
    )
    new_index = wiki_compile.upsert_index_entry(
        index_md, title, filename, wiki_compile.summarize(content)
    )
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=root_id, name=wiki_compile.INDEX_PAGE, content=new_index.encode(),
    )
    _, log_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.LOG_PAGE
    )
    new_log = wiki_compile.append_log(
        log_md, wiki_compile.log_entry("promote", filename, [])
    )
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=root_id, name=wiki_compile.LOG_PAGE, content=new_log.encode(),
    )
    _log.info("wiki_answer_promoted", message_id=message_id, page=filename)
    return page


# --- Lint (결정적 자동 수정 + 휴리스틱 리포트, wiki-v2 D6/D9) -----------------


@dataclass(frozen=True)
class LintReport:
    """Lint 결과. auto_fixed 는 결정적 자동 수정, reports 는 사람 판단이 필요한 안내."""

    auto_fixed: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)


async def lint_wiki(
    session: AsyncSession, storage: StorageService, user: User
) -> LintReport:
    """수동 Lint 트리거(wiki-v2 4.2). 로그인 사용자 누구나(결정적 자동 수정뿐, 위험 낮음 — D6)."""
    root_id, owner = await _ensure_wiki(session, storage)
    return await run_lint(session, storage, root_id, owner)


async def run_lint(
    session: AsyncSession,
    storage: StorageService,
    root_id: int,
    owner: User,
) -> LintReport:
    """위키 Lint — index 정합성 자동 수정 + stale 소스/소유자 변경/깨진 링크 리포트(wiki-v2 D9).

    결정적 자동 수정은 index.md 정합성만(사라진 페이지 항목 제거·누락 페이지 항목 추가). 링크·
    사실 판단은 리포트만 하고 수정하지 않는다. 리포트는 log.md append + 잡 결과로 남긴다.
    """
    report = LintReport()

    pages = await wiki_compile._list_pages(session, storage, root_id)
    page_names = {
        p.name
        for p in pages
        if p.name not in (wiki_compile.INDEX_PAGE, wiki_compile.LOG_PAGE)
    }

    # 결정적: index.md 정합화(사라진 페이지 항목 제거 + 누락 페이지 항목 추가).
    _, index_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.INDEX_PAGE
    )
    indexed = _indexed_filenames(index_md)
    stale_entries = indexed - page_names
    missing_entries = page_names - indexed
    new_index = index_md
    for fname in sorted(stale_entries):
        new_index = _drop_index_entry(new_index, fname)
        report.auto_fixed.append(f"index 에서 사라진 페이지 항목 제거: {fname}")
    for fname in sorted(missing_entries):
        title = fname[:-3] if fname.endswith(".md") else fname
        new_index = wiki_compile.upsert_index_entry(new_index, title, fname, "")
        report.auto_fixed.append(f"index 에 누락 페이지 항목 추가: {fname}")
    if new_index != index_md:
        await files_service.write_text_file_as(
            session, storage, owner,
            parent_id=root_id, name=wiki_compile.INDEX_PAGE, content=new_index.encode(),
        )

    # 리포트: 페이지 간 깨진 내부 링크(.md 링크가 실재 페이지가 아님).
    for page in pages:
        for target in _internal_links(page.content):
            if target not in page_names and target not in (
                wiki_compile.INDEX_PAGE,
                wiki_compile.LOG_PAGE,
            ):
                report.reports.append(f"깨진 내부 링크: {page.name} → {target}")

    # 리포트: 소스 버전 갱신(stale) / 소유자 변경·삭제 / 고아 소스.
    src_rows = (
        await session.execute(select(WikiSource))
    ).scalars().all()
    for src in src_rows:
        file = await files_service.get_file(session, src.file_id)
        if file is None or file.is_deleted:
            report.reports.append(f"고아 소스(파일 없음/삭제됨): file={src.file_id}")
            continue
        if (
            src.last_ingested_version is not None
            and not file.is_folder
            and file.current_version > src.last_ingested_version
        ):
            src.status = SOURCE_STALE
            report.reports.append(
                f"stale 소스(버전 갱신 v{src.last_ingested_version}→v{file.current_version}): "
                f"{file.name} — 재컴파일 권장"
            )
        if file.user_id != src.added_by:
            report.reports.append(
                f"소스 소유자 변경됨(added_by 불일치): {file.name} — 재컴파일/제거 제안"
            )

    # log.md 에 리포트 요약 append.
    _, log_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.LOG_PAGE
    )
    summary = f"lint auto_fixed={len(report.auto_fixed)} reports={len(report.reports)}"
    new_log = wiki_compile.append_log(
        log_md, wiki_compile.log_entry("lint", summary, [])
    )
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=root_id, name=wiki_compile.LOG_PAGE, content=new_log.encode(),
    )
    await session.commit()
    return report


# 마크다운 링크 `[텍스트](대상)` 매처(Lint 의 index 정합성·깨진 링크 판정용).
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _indexed_filenames(index_md: str) -> set[str]:
    names: set[str] = set()
    for line in index_md.splitlines():
        for m in _MD_LINK.finditer(line):
            target = m.group(2)
            if target.endswith(".md"):
                names.add(target)
    return names


def _drop_index_entry(index_md: str, filename: str) -> str:
    marker = f"]({filename})"
    kept = [
        line
        for line in index_md.splitlines()
        if not (line.startswith("- [") and marker in line)
    ]
    return "\n".join(kept).rstrip() + "\n"


def _internal_links(md: str) -> set[str]:
    return {m.group(2) for m in _MD_LINK.finditer(md) if m.group(2).endswith(".md")}
