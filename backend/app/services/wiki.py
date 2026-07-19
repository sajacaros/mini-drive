"""위키 스페이스 서비스 (LLM 위키, PRD 3.7.1~3.7.2, 5.14, 6.8, Phase 7-3).

핵심 불변식 — **권한 경계 = 컴파일 경계**(PRD 3.7.1):
  - group 스코프 스페이스의 루트 폴더에는 그 그룹의 read 권한을 자동 부여(inherit_to_children)
    하므로 "위키 페이지를 읽을 수 있는 사람 = 그룹 멤버"가 구조적으로 보장된다.
  - 소스 등록 시 스코프의 read 권한을 검증한다 — personal 은 소유자(user_id) 자격, group 은
    **그 그룹**이 해당 파일에 read 이상을 가져야 한다(개인 자격이 아니라 그룹 기준).

위키 페이지 자체는 별도 테이블이 아니라 root_folder_id 하위의 드라이브 파일이므로, 조회/버전/
이름 변경은 기존 파일 API 를 그대로 쓴다. 이 서비스는 스페이스/소스/잡의 수명주기와 접근 인가,
그리고 컴파일 범위(스코프 파일 집합) 산출을 담당한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import arq_queue
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import (
    File,
    User,
    WikiJob,
    WikiSource,
    WikiSpace,
)
from app.models.enums import GroupPermission, GroupRole
from app.models.wiki import (
    JOB_DONE,
    JOB_FAILED,
    JOB_INGEST,
    JOB_QUEUED,
    JOB_RUNNING,
    SCOPE_GROUP,
    SCOPE_PERSONAL,
    SOURCE_FAILED,
    SOURCE_INDEXED,
    SOURCE_QUEUED,
    SOURCE_STALE,
)
from app.services import files as files_service
from app.services import groups as groups_service
from app.services import indexing as indexing_service
from app.services import permissions as permissions_service
from app.services import wiki_compile
from app.services.chat_llm import ChatProvider
from app.services.storage import StorageService

_log = get_logger("app.wiki")

# 그룹 스코프 스페이스를 만들거나 소스를 관리할 수 있는 그룹 역할.
_SPACE_MANAGERS = frozenset({GroupRole.OWNER, GroupRole.ADMIN})

# 상세 조회에 포함할 최근 log.md 항목 수.
_RECENT_LOG_LIMIT = 20

# 초기 카탈로그/로그 스텁.
_INDEX_STUB = "# 위키 카탈로그\n\n(아직 페이지가 없습니다. 소스를 등록하면 컴파일됩니다.)\n"
_LOG_STUB = "# 컴파일 로그\n"


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


# --- 접근 인가 --------------------------------------------------------------


async def _get_space(session: AsyncSession, space_id: int) -> WikiSpace:
    space = await session.get(WikiSpace, space_id)
    if space is None:
        raise WikiServiceError(404, "위키 스페이스를 찾을 수 없습니다.")
    return space


async def can_access_space(
    session: AsyncSession, user: User, space: WikiSpace
) -> bool:
    """스페이스 조회 자격 — personal 은 소유자, group 은 활성 멤버."""
    if space.scope == SCOPE_PERSONAL:
        return space.user_id == user.id
    role = await groups_service.get_member_role(session, space.group_id, user.id)
    return role is not None


async def ensure_access(
    session: AsyncSession, user: User, space: WikiSpace
) -> None:
    """스페이스 조회 인가. 접근 불가면 404(존재 은닉, admin 도 예외 없음 — PRD 3.7.3)."""
    if not await can_access_space(session, user, space):
        raise WikiServiceError(404, "위키 스페이스를 찾을 수 없습니다.")


async def ensure_write(
    session: AsyncSession, user: User, space: WikiSpace
) -> None:
    """스페이스 쓰기 인가(소스 관리·삭제·승격) — personal 은 소유자, group 은 admin 이상.

    조회조차 불가하면 404, 조회는 되지만 쓰기 부족이면 403.
    """
    if space.scope == SCOPE_PERSONAL:
        if space.user_id != user.id:
            raise WikiServiceError(404, "위키 스페이스를 찾을 수 없습니다.")
        return
    role = await groups_service.get_member_role(session, space.group_id, user.id)
    if role is None:
        raise WikiServiceError(404, "위키 스페이스를 찾을 수 없습니다.")
    if role not in _SPACE_MANAGERS:
        raise WikiServiceError(403, "이 스페이스를 관리할 권한이 없습니다 (그룹 admin 이상).")


async def page_owner(session: AsyncSession, space: WikiSpace) -> User:
    """위키 페이지를 쓰는 자격(루트 폴더 소유자 = 스페이스 생성자)을 반환한다."""
    root = await session.get(File, space.root_folder_id)
    if root is None:
        raise WikiServiceError(500, "위키 루트 폴더가 없습니다.")
    owner = await session.get(User, root.user_id)
    if owner is None:
        raise WikiServiceError(500, "위키 페이지 소유자를 찾을 수 없습니다.")
    return owner


# --- 소스 스코프 read 검증 (권한 경계 = 컴파일 경계) -------------------------


async def _user_can_read(
    session: AsyncSession, user: User, file: File
) -> bool:
    if file.user_id == user.id:
        return True
    level = await permissions_service.get_access_level(session, user, file)
    return level is not None


async def scope_can_read(
    session: AsyncSession, space: WikiSpace, file: File
) -> bool:
    """스코프가 파일을 read 가능한지(등록 가능 여부, PRD 3.7.1 불변식).

    personal → 소유자(user_id) 자격, group → **그 그룹**이 파일에 read 이상.
    """
    if space.scope == SCOPE_PERSONAL:
        owner = await session.get(User, space.user_id)
        if owner is None:
            return False
        return await _user_can_read(session, owner, file)
    level = await permissions_service.group_access_level(
        session, space.group_id, file.id
    )
    return level is not None


# --- 스코프 파일 집합 (챗 범위 제한) -----------------------------------------


async def _subtree_ids(session: AsyncSession, root_id: int) -> list[int]:
    rows = await session.execute(
        text(_SUBTREE_CTE + " SELECT id FROM sub"), {"root": root_id}
    )
    return [r.id for r in rows]


@dataclass(frozen=True)
class SpaceScope:
    """챗 범위 제한용 스페이스 파일 집합. wiki_page_ids 는 우선순위 배치에 쓴다."""

    all_ids: set[int]
    wiki_page_ids: set[int]


async def space_scope(session: AsyncSession, space: WikiSpace) -> SpaceScope:
    """스페이스 범위의 파일 집합 = 루트 폴더 서브트리(위키 페이지) ∪ 등록 소스 서브트리.

    챗 세션이 이 스페이스로 범위 지정되면 검색 후보를 이 집합으로 교집합한다(PRD 3.7.5).
    """
    wiki_ids = set(await _subtree_ids(session, space.root_folder_id))
    all_ids = set(wiki_ids)
    sources = (
        await session.execute(
            select(WikiSource.file_id, WikiSource.recursive).where(
                WikiSource.space_id == space.id
            )
        )
    ).all()
    for file_id, recursive in sources:
        if recursive:
            all_ids.update(await _subtree_ids(session, file_id))
        else:
            all_ids.add(file_id)
    return SpaceScope(all_ids=all_ids, wiki_page_ids=wiki_ids)


# --- 잡 기록 ----------------------------------------------------------------


async def record_job(
    session: AsyncSession,
    space_id: int,
    kind: str,
    *,
    file_id: int | None = None,
    status: str = JOB_QUEUED,
    error: str | None = None,
) -> WikiJob:
    """wiki_jobs 이력 한 건을 기록한다(커밋은 호출자 조율). 상태 조회·재시도 판단의 진실 소스."""
    job = WikiJob(
        space_id=space_id, file_id=file_id, kind=kind, status=status, error=error
    )
    session.add(job)
    return job


# --- 스페이스 생성 (PRD 6.8) -------------------------------------------------


async def create_space(
    session: AsyncSession,
    storage: StorageService,
    actor: User,
    *,
    name: str,
    scope: str,
    group_id: int | None,
) -> WikiSpace:
    """위키 스페이스를 만든다(루트 폴더 + index.md/log.md + group read 자동 부여, PRD 6.8/3.7.1)."""
    name = name.strip()
    if not name:
        raise WikiServiceError(422, "스페이스 이름이 비어 있습니다.")
    if scope not in (SCOPE_PERSONAL, SCOPE_GROUP):
        raise WikiServiceError(422, "scope 는 personal 또는 group 이어야 합니다.")

    if scope == SCOPE_GROUP:
        if group_id is None:
            raise WikiServiceError(422, "group 스코프는 group_id 가 필요합니다.")
        # 그룹 admin 이상만 그룹 스페이스를 만들 수 있다(PRD 6.8).
        try:
            await groups_service.require_group_role(
                session, group_id, actor.id, _SPACE_MANAGERS
            )
        except groups_service.GroupServiceError as exc:
            raise WikiServiceError(exc.status_code, exc.detail) from exc

    # 1) 루트 폴더 생성(생성자 소유). 이름 충돌 시 409.
    try:
        root = await files_service.create_folder(session, actor, name, None)
    except files_service.FileServiceError as exc:
        raise WikiServiceError(exc.status_code, exc.detail) from exc

    # 2) group 스코프면 루트 폴더에 그룹 read(inherit) 자동 부여 — 페이지 읽기 = 그룹 멤버.
    if scope == SCOPE_GROUP:
        try:
            await permissions_service.grant_permission(
                session,
                actor,
                root.id,
                group_id,
                GroupPermission.READ,
                inherit_to_children=True,
                expires_at=None,
            )
        except permissions_service.PermissionServiceError as exc:
            raise WikiServiceError(exc.status_code, exc.detail) from exc

    # 3) wiki_spaces 행 생성.
    space = WikiSpace(
        name=name,
        scope=scope,
        user_id=actor.id if scope == SCOPE_PERSONAL else None,
        group_id=group_id if scope == SCOPE_GROUP else None,
        root_folder_id=root.id,
        settings={},
    )
    session.add(space)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise WikiServiceError(409, "스페이스 생성에 실패했습니다.") from exc
    await session.refresh(space)

    # 4) 초기 카탈로그/로그 페이지 생성(드라이브 파일 업로드 경로 재사용).
    await files_service.write_text_file_as(
        session, storage, actor,
        parent_id=root.id, name=wiki_compile.INDEX_PAGE, content=_INDEX_STUB.encode(),
    )
    await files_service.write_text_file_as(
        session, storage, actor,
        parent_id=root.id, name=wiki_compile.LOG_PAGE, content=_LOG_STUB.encode(),
    )
    _log.info("wiki_space_created", space_id=space.id, scope=scope, actor=actor.id)
    return space


# --- 소스 등록 / 제거 (PRD 6.8) ---------------------------------------------


async def register_source(
    session: AsyncSession,
    actor: User,
    space_id: int,
    *,
    file_id: int,
    recursive: bool,
) -> WikiSource:
    """소스를 등록한다 — 스코프 read 검증 후 Ingest 잡을 큐잉한다(PRD 6.8/3.7.1).

    실패 시 403(스코프 read 불가). 성공 시 wiki_sources upsert + wiki_jobs(ingest) 기록 +
    arq wiki_ingest(space_id, file_id) 큐잉.
    """
    space = await _get_space(session, space_id)
    await ensure_write(session, actor, space)

    file = await files_service.get_file(session, file_id)
    if file is None or file.is_deleted:
        raise WikiServiceError(404, "소스 파일을 찾을 수 없습니다.")

    # 권한 경계 = 컴파일 경계 — 스코프가 read 불가한 파일은 등록 거부(PRD 3.7.1).
    if not await scope_can_read(session, space, file):
        raise WikiServiceError(
            403, "스코프가 이 파일을 읽을 수 없어 소스로 등록할 수 없습니다."
        )

    existing = await session.get(WikiSource, (space_id, file_id))
    if existing is None:
        source = WikiSource(
            space_id=space_id,
            file_id=file_id,
            recursive=recursive,
            status=SOURCE_QUEUED,
            added_by=actor.id,
        )
        session.add(source)
    else:
        existing.recursive = recursive
        existing.status = SOURCE_QUEUED
        source = existing

    await record_job(session, space_id, JOB_INGEST, file_id=file_id)
    await session.commit()
    await session.refresh(source)

    # arq 큐잉(실패는 경고만 — fail-open, 파일 서비스 철학). 워커가 컴파일을 수행한다.
    await arq_queue.enqueue_wiki_ingest(space_id, file_id)
    _log.info("wiki_source_registered", space_id=space_id, file_id=file_id)
    return source


async def remove_source(
    session: AsyncSession,
    storage: StorageService,
    actor: User,
    space_id: int,
    file_id: int,
) -> None:
    """소스를 제거한다(PRD 6.8). 페이지 자동 삭제는 하지 않고 log.md 에 기록한다(Lint 로 안내).

    소스 파일의 청크는 지우지 않는다 — 파일 자체는 드라이브에 남아 있고 다른 경로로 검색될 수
    있으므로(위키 소스 등록만 해제한다).
    """
    space = await _get_space(session, space_id)
    await ensure_write(session, actor, space)

    existing = await session.get(WikiSource, (space_id, file_id))
    if existing is None:
        raise WikiServiceError(404, "등록된 소스가 아닙니다.")
    await session.delete(existing)
    await session.commit()

    # log.md 에 제거 기록(관련 페이지 stale 안내는 Lint 리포트가 담당).
    owner = await page_owner(session, space)
    _, log_md = await wiki_compile._read_page_text(
        session, storage, space.root_folder_id, wiki_compile.LOG_PAGE
    )
    entry = wiki_compile.log_entry("source-removed", f"file:{file_id}", [])
    new_log = wiki_compile.append_log(log_md, entry)
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=space.root_folder_id, name=wiki_compile.LOG_PAGE,
        content=new_log.encode(),
    )
    _log.info("wiki_source_removed", space_id=space_id, file_id=file_id)


# --- 목록 / 상세 / 삭제 (PRD 6.8) -------------------------------------------


async def list_spaces(session: AsyncSession, user: User) -> list[WikiSpace]:
    """내가 접근 가능한 스페이스 — personal 소유 + 소속 그룹의 group 스페이스."""
    group_ids = await groups_service.get_user_group_ids(session, user.id)
    condition = WikiSpace.user_id == user.id
    if group_ids:
        condition = condition | WikiSpace.group_id.in_(group_ids)
    rows = (
        await session.execute(
            select(WikiSpace)
            .where(condition)
            .order_by(WikiSpace.created_at.desc(), WikiSpace.id.desc())
        )
    ).scalars().all()
    return list(rows)


@dataclass(frozen=True)
class SourceInfo:
    file_id: int
    file_name: str
    recursive: bool
    status: str
    last_ingested_version: int | None


@dataclass(frozen=True)
class SpaceDetail:
    space: WikiSpace
    sources: list[SourceInfo]
    recent_log: list[str]
    index_entries: list[str]


async def get_detail(
    session: AsyncSession, storage: StorageService, user: User, space_id: int
) -> SpaceDetail:
    """스페이스 상세 — 소스 목록·상태, 최근 log.md 항목, index.md 카탈로그 요약(PRD 6.8)."""
    space = await _get_space(session, space_id)
    await ensure_access(session, user, space)

    src_rows = (
        await session.execute(
            select(WikiSource, File.name)
            .join(File, File.id == WikiSource.file_id)
            .where(WikiSource.space_id == space_id)
            .order_by(WikiSource.created_at.asc())
        )
    ).all()
    sources = [
        SourceInfo(
            file_id=s.file_id,
            file_name=name,
            recursive=s.recursive,
            status=s.status,
            last_ingested_version=s.last_ingested_version,
        )
        for s, name in src_rows
    ]

    _, index_md = await wiki_compile._read_page_text(
        session, storage, space.root_folder_id, wiki_compile.INDEX_PAGE
    )
    _, log_md = await wiki_compile._read_page_text(
        session, storage, space.root_folder_id, wiki_compile.LOG_PAGE
    )
    return SpaceDetail(
        space=space,
        sources=sources,
        recent_log=wiki_compile.recent_log_entries(log_md, _RECENT_LOG_LIMIT),
        index_entries=wiki_compile.index_entries(index_md),
    )


async def list_jobs(
    session: AsyncSession, user: User, space_id: int, limit: int = 50
) -> list[WikiJob]:
    """스페이스 잡 이력(최근순). 스페이스 접근자만(admin 도 스페이스 접근 자격 필요, PRD 6.8)."""
    space = await _get_space(session, space_id)
    await ensure_access(session, user, space)
    rows = (
        await session.execute(
            select(WikiJob)
            .where(WikiJob.space_id == space_id)
            .order_by(WikiJob.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def delete_space(
    session: AsyncSession, user: User, space_id: int
) -> None:
    """스페이스를 삭제한다(PRD 6.8). wiki_* rows CASCADE, 위키 페이지 폴더는 드라이브에 잔존.

    chat_sessions.space_id 는 FK ON DELETE SET NULL 로 세션은 남고 범위만 해제된다.
    """
    space = await _get_space(session, space_id)
    await ensure_write(session, user, space)
    await session.execute(delete(WikiSpace).where(WikiSpace.id == space_id))
    await session.commit()
    _log.info("wiki_space_deleted", space_id=space_id, actor=user.id)


# --- Ingest 오케스트레이션 (워커 태스크가 호출, PRD 3.7.1) ------------------


@dataclass(frozen=True)
class IngestSummary:
    """Ingest 잡 결과 요약. compiled 는 페이지가 생성/갱신된 소스 파일 수."""

    status: str
    compiled: int
    targets: int


async def ingest_source(
    session: AsyncSession,
    storage: StorageService,
    chat_provider: ChatProvider,
    settings: Settings,
    space_id: int,
    file_id: int,
) -> IngestSummary:
    """소스 하나(파일 또는 재귀 폴더)를 컴파일한다 — 워커 wiki_ingest 태스크의 본체.

    폴더+recursive 면 적격 하위 파일로 팬아웃해 순차 컴파일한다(파일당 1 페이지 컴파일). 잡·소스
    상태를 진실 소스로 갱신한다(PRD 5.15). 실패는 wiki_jobs failed+error 로 남기고 예외를 다시
    던져 arq 재시도(최대 3회)에 맡긴다.
    """
    space = await session.get(WikiSpace, space_id)
    source = await session.get(WikiSource, (space_id, file_id))
    if space is None or source is None:
        return IngestSummary("skipped", 0, 0)  # 스페이스/소스가 사라짐(경합)

    owner = await page_owner(session, space)
    job = await record_job(
        session, space_id, JOB_INGEST, file_id=file_id, status=JOB_RUNNING
    )
    await session.commit()
    job_id = job.id

    try:
        file = await files_service.get_file(session, file_id)
        if file is None or file.is_deleted:
            raise WikiServiceError(404, "소스 파일을 찾을 수 없습니다.")
        # 컴파일 중 내부 커밋으로 file 이 만료되므로 버전을 미리 캡처한다(async lazy-load 회피).
        source_version = file.current_version
        is_folder = file.is_folder

        if is_folder:
            target_ids = (
                await indexing_service.eligible_descendant_files(session, file_id)
                if source.recursive
                else []
            )
        else:
            target_ids = [file_id]

        compiled = 0
        for tid in target_ids:
            tfile = await files_service.get_file(session, tid)
            if tfile is None or tfile.is_deleted or tfile.is_folder:
                continue
            outcome = await wiki_compile.compile_source(
                session,
                storage,
                chat_provider,
                settings,
                root_folder_id=space.root_folder_id,
                owner=owner,
                source_file=tfile,
            )
            if outcome.status == "compiled":
                compiled += 1
    except Exception as exc:  # noqa: BLE001 - 실패를 기록하고 arq 재시도에 맡긴다.
        await session.rollback()
        src = await session.get(WikiSource, (space_id, file_id))
        if src is not None:
            src.status = SOURCE_FAILED
        failed_job = await session.get(WikiJob, job_id)
        if failed_job is not None:
            failed_job.status = JOB_FAILED
            failed_job.error = str(exc)[:2000]
        await session.commit()
        _log.warning(
            "wiki_ingest_failed", space_id=space_id, file_id=file_id, error=str(exc)
        )
        raise

    # 성공 — 소스/잡 상태 갱신(내부 컴파일 커밋으로 만료됐을 수 있어 재조회).
    src = await session.get(WikiSource, (space_id, file_id))
    if src is not None:
        src.status = SOURCE_INDEXED
        src.last_ingested_version = source_version
    done_job = await session.get(WikiJob, job_id)
    if done_job is not None:
        done_job.status = JOB_DONE
    await session.commit()
    _log.info(
        "wiki_ingest_done",
        space_id=space_id,
        file_id=file_id,
        targets=len(target_ids),
        compiled=compiled,
    )
    return IngestSummary("done", compiled, len(target_ids))


# --- 답변 승격 (PRD 6.9) -----------------------------------------------------


async def promote_answer(
    session: AsyncSession,
    storage: StorageService,
    actor: User,
    space_id: int,
    *,
    message_id: int,
    title: str,
    content: str,
    citations: list[dict] | None,
) -> File:
    """챗 답변을 위키 페이지로 승격한다(PRD 6.9). 스페이스 쓰기 자격 필요.

    frontmatter 에 `promoted_from`(원본 메시지 id)·`locked: false` 를 붙이고 답변 본문 + 출처
    인용을 담아 페이지를 쓴다(index.md/log.md 부기 포함). 인용 링크는 클릭 시점에 다시
    ensure_file_access 를 통과하므로 승격이 권한을 넓히지 않는다.
    """
    title = title.strip()
    if not title:
        raise WikiServiceError(422, "페이지 제목이 비어 있습니다.")
    space = await _get_space(session, space_id)
    await ensure_write(session, actor, space)
    owner = await page_owner(session, space)

    filename = wiki_compile.page_filename(title)
    existing, existing_md = await wiki_compile._read_page_text(
        session, storage, space.root_folder_id, filename
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
        parent_id=space.root_folder_id, name=filename, content=page_md.encode(),
    )

    # 부기 — index.md 갱신 + log.md append(승격).
    _, index_md = await wiki_compile._read_page_text(
        session, storage, space.root_folder_id, wiki_compile.INDEX_PAGE
    )
    new_index = wiki_compile.upsert_index_entry(
        index_md, title, filename, wiki_compile.summarize(content)
    )
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=space.root_folder_id, name=wiki_compile.INDEX_PAGE,
        content=new_index.encode(),
    )
    _, log_md = await wiki_compile._read_page_text(
        session, storage, space.root_folder_id, wiki_compile.LOG_PAGE
    )
    new_log = wiki_compile.append_log(
        log_md, wiki_compile.log_entry("promote", filename, [])
    )
    await files_service.write_text_file_as(
        session, storage, owner,
        parent_id=space.root_folder_id, name=wiki_compile.LOG_PAGE,
        content=new_log.encode(),
    )
    _log.info(
        "wiki_answer_promoted", space_id=space_id, message_id=message_id, page=filename
    )
    return page


# --- Lint (결정적 자동 수정 + 휴리스틱 리포트, PRD 3.7.1) --------------------


@dataclass(frozen=True)
class LintReport:
    """Lint 결과. auto_fixed 는 결정적 자동 수정, reports 는 사람 판단이 필요한 안내."""

    auto_fixed: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)


async def lint_space(
    session: AsyncSession, storage: StorageService, user: User, space_id: int
) -> LintReport:
    """수동 Lint 트리거(PRD 6.8) — 쓰기 자격 검증 후 run_lint 를 실행한다."""
    space = await _get_space(session, space_id)
    await ensure_write(session, user, space)
    return await run_lint(session, storage, space)


async def run_lint(
    session: AsyncSession, storage: StorageService, space: WikiSpace
) -> LintReport:
    """스페이스 Lint — index 정합성 자동 수정 + stale 소스/권한 회수/깨진 링크 리포트(PRD 3.7.1).

    결정적 자동 수정은 index.md 정합성만(사라진 페이지 항목 제거·누락 페이지 항목 추가). 링크·
    사실 판단은 리포트만 하고 수정하지 않는다. 리포트는 log.md append + 잡 결과로 남긴다.
    """
    report = LintReport()
    owner = await page_owner(session, space)
    root_id = space.root_folder_id

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
            parent_id=root_id, name=wiki_compile.INDEX_PAGE,
            content=new_index.encode(),
        )

    # 리포트: 페이지 간 깨진 내부 링크(.md 링크가 실재 페이지가 아님).
    for page in pages:
        for target in _internal_links(page.content):
            if target not in page_names and target not in (
                wiki_compile.INDEX_PAGE,
                wiki_compile.LOG_PAGE,
            ):
                report.reports.append(
                    f"깨진 내부 링크: {page.name} → {target}"
                )

    # 리포트: 소스 버전 갱신(stale) / 권한 회수 / 고아 소스.
    src_rows = (
        await session.execute(
            select(WikiSource).where(WikiSource.space_id == space.id)
        )
    ).scalars().all()
    for src in src_rows:
        file = await files_service.get_file(session, src.file_id)
        if file is None or file.is_deleted:
            report.reports.append(f"고아 소스(파일 없음/삭제됨): file={src.file_id}")
            continue
        if (
            src.last_ingested_version is not None
            and file.current_version > src.last_ingested_version
        ):
            src.status = SOURCE_STALE
            report.reports.append(
                f"stale 소스(버전 갱신 v{src.last_ingested_version}→v{file.current_version}): "
                f"{file.name} — 재컴파일 권장"
            )
        if not await scope_can_read(session, space, file):
            report.reports.append(
                f"소스 권한 회수됨(스코프 read 불가): {file.name} — 재컴파일/제거 제안"
            )

    # log.md 에 리포트 요약 append.
    _, log_md = await wiki_compile._read_page_text(
        session, storage, root_id, wiki_compile.LOG_PAGE
    )
    summary = (
        f"lint auto_fixed={len(report.auto_fixed)} reports={len(report.reports)}"
    )
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
