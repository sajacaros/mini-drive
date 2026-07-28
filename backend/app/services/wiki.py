"""위키 인덱싱 토글 서비스 (spec/wiki-index.md).

이 모듈이 다루는 것은 **인덱싱 여부** 하나다. 누가 질의할 수 있는지는 그 파일의 기존 권한이
결정하고, 전사 공개는 `@전사` 그룹에 read 를 주는 것이라 permissions 서비스가 그대로 처리한다.
위키가 권한 체계를 새로 만들지 않는다는 게 설계의 뼈대다.

`files.wiki_enabled` 는 3상태다 — NULL(상속) / TRUE(명시 ON) / FALSE(명시 OFF).
판정은 **가장 가까운 명시값이 이긴다**. 권한 판정이 누적(union)인 것과 방향이 반대인데,
성격이 다르기 때문이다:

  - 권한은 *능력의 합*이다. 여러 그룹에서 받은 것을 모두 가지는 게 자연스럽다.
  - 위키는 *설정의 캐스케이드*다. 상위에서 켠 것을 하위에서 끌 수 있어야 한다 —
    manage 권한자가 폴더를 발행할 때 소유자가 자기 파일만 빼는 탈출구가 명시 FALSE 다.
    "체인에 TRUE 가 하나라도 있으면 ON"으로 하면 그 탈출구가 사라진다.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AuditLog, File, User
from app.models.enums import GroupPermission
from app.services import permissions as permissions_service

# md 경로만 쓴다. PDF 는 목차 판정 루프에서 막히고, 사내 PDF 의 43% 는 추출 텍스트가 아예
# 없어 OCR 이 필요하다 — 둘 다 v1 범위 밖이다(spec/wiki-index.md 「왜 PDF 가 v1 에 없는가」).
INDEXABLE_EXTENSIONS = frozenset({".md", ".markdown", ".html", ".htm"})


class WikiServiceError(Exception):
    """위키 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class IndexableVerdict:
    """인덱싱 대상 판정 결과. 대상이 아니면 사용자에게 보여줄 이유를 담는다.

    토글을 조용히 무시하지 않고 **비활성 + 이유**로 노출하기 위한 것이다 — 켰는데 아무 일도
    일어나지 않는 상태가 가장 나쁘다.
    """

    ok: bool
    reason: str | None = None


def indexable(file: File) -> IndexableVerdict:
    """이 파일이 인덱싱 대상인가. 폴더는 대상이 아니다(하위 파일이 대상이다)."""
    settings = get_settings()
    if file.is_folder:
        return IndexableVerdict(False, "폴더 자체는 인덱싱 대상이 아닙니다.")
    if file.is_deleted:
        return IndexableVerdict(False, "휴지통에 있는 항목은 인덱싱할 수 없습니다.")
    ext = posixpath.splitext(file.name.lower())[1]
    if ext not in INDEXABLE_EXTENSIONS:
        return IndexableVerdict(
            False, "지원하지 않는 형식입니다 (현재 Markdown·HTML 만)."
        )
    if file.size > settings.wiki_max_input_bytes:
        limit_mb = settings.wiki_max_input_bytes / (1024 * 1024)
        return IndexableVerdict(False, f"파일이 너무 큽니다 (상한 {limit_mb:.0f}MB).")
    return IndexableVerdict(True)


# 대상 파일에서 루트까지 올라가며 wiki_enabled 명시값(NOT NULL)만 모은다. depth 0 = 자기 자신.
_ANCESTOR_WIKI_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id, wiki_enabled, 0 AS depth
        FROM files WHERE id = :file_id
        UNION ALL
        SELECT f.id, f.parent_folder_id, f.wiki_enabled, a.depth + 1
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT depth, id AS file_id, wiki_enabled
    FROM ancestors
    WHERE wiki_enabled IS NOT NULL
    ORDER BY depth ASC
    LIMIT 1
    """
)


@dataclass(frozen=True)
class WikiState:
    """파일 하나의 위키 상태. `source_file_id` 는 그 값이 어디서 왔는지(상속 출처)."""

    enabled: bool
    explicit: bool  # 이 파일 자신에 명시값이 있는가 (UI 의 '상속됨' 표기용)
    source_file_id: int | None


async def resolve_wiki_state(session: AsyncSession, file_id: int) -> WikiState:
    """조상 경로에서 가장 가까운 명시값을 찾아 위키 상태를 판정한다. 없으면 꺼짐."""
    row = (await session.execute(_ANCESTOR_WIKI_SQL, {"file_id": file_id})).first()
    if row is None:
        return WikiState(enabled=False, explicit=False, source_file_id=None)
    return WikiState(
        enabled=bool(row.wiki_enabled),
        explicit=row.depth == 0,
        source_file_id=row.file_id,
    )


# 폴더 하위(자기 자신 제외)의 살아있는 파일을 확장자별로 센다. 폴더 토글 확인 문구용 —
# "md·html 12개가 인덱싱됩니다 (PDF 8개, pptx 3개는 제외)" 를 보여주려면 제외 대상도 세야 한다.
_DESCENDANT_FILES_SQL = text(
    """
    WITH RECURSIVE tree AS (
        SELECT id FROM files WHERE id = :folder_id
        UNION ALL
        SELECT f.id FROM files f JOIN tree t ON f.parent_folder_id = t.id
    )
    SELECT f.id, f.name, f.size, f.user_id, f.is_folder, f.is_deleted
    FROM files f JOIN tree t ON f.id = t.id
    WHERE f.id <> :folder_id AND f.is_folder = FALSE AND f.is_deleted = FALSE
    """
)


@dataclass(frozen=True)
class FolderScope:
    """폴더 토글이 실제로 덮는 범위. 확인 문구와 인덱싱 큐 투입에 함께 쓴다."""

    target_ids: list[int]
    target_count: int
    skipped_by_format: int
    skipped_by_size: int
    skipped_by_permission: int


async def folder_scope(
    session: AsyncSession, actor: User, folder: File
) -> FolderScope:
    """폴더 하위에서 실제로 인덱싱될 파일 집합을 산출한다.

    **파일마다 manage 를 다시 판정한다.** 폴더에 manage 가 있어도 상위 행의
    inherit_to_children=FALSE·만료·소유자 변경으로 개별 파일에서 성립하지 않을 수 있다.
    틀리더라도 발행 누락 방향이라 안전하다(spec/wiki-index.md).
    """
    rows = (
        await session.execute(_DESCENDANT_FILES_SQL, {"folder_id": folder.id})
    ).all()

    settings = get_settings()
    targets: list[int] = []
    by_format = by_size = by_perm = 0
    for r in rows:
        ext = posixpath.splitext(r.name.lower())[1]
        if ext not in INDEXABLE_EXTENSIONS:
            by_format += 1
            continue
        if r.size > settings.wiki_max_input_bytes:
            by_size += 1
            continue
        if not await _can_publish(session, actor, r.id, r.user_id):
            by_perm += 1
            continue
        targets.append(r.id)

    return FolderScope(
        target_ids=targets,
        target_count=len(targets),
        skipped_by_format=by_format,
        skipped_by_size=by_size,
        skipped_by_permission=by_perm,
    )


async def _can_publish(
    session: AsyncSession, actor: User, file_id: int, owner_id: int
) -> bool:
    """이 사용자가 이 파일을 발행할 수 있는가 — 소유자이거나 manage 권한자.

    manage 는 이미 그 파일에 `@전사 read` 를 직접 부여할 수 있는 권한이다. 폴더 토글은 새
    권한을 만드는 게 아니라 그 행위의 지름길이므로 같은 기준을 쓴다.
    """
    if owner_id == actor.id:
        return True
    stub = await session.get(File, file_id)
    if stub is None:
        return False
    level = await permissions_service.get_access_level(session, actor, stub)
    return level is not None and permissions_service.permission_covers(level, "manage")


def _record_audit(
    session: AsyncSession,
    actor_id: int,
    action: str,
    target_id: int,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type="file",
            target_id=target_id,
            detail=detail,
        )
    )


async def set_wiki(
    session: AsyncSession,
    actor: User,
    file_id: int,
    *,
    enabled: bool | None = None,
    enabled_set: bool = False,
    public: bool | None = None,
) -> WikiState:
    """위키 인덱싱 on/off + 전사 공개 여부.

    두 축은 **독립**이다. (끔, 공개) 조합은 "전사 공유하되 질의 대상은 아님"으로 정상이며,
    규정상 LLM 전송이 금지된 자료나 인덱싱 실익이 없는 파일(이미지·영상)에 쓴다.

    enabled_set : 인덱싱 설정을 건드리는가. `enabled=None`(상속으로 되돌리기)과 '생략'을
                  구분해야 해서 별도 플래그를 둔다 — update_permission 의 expires_at_set 과 같은 결.
    enabled     : files.wiki_enabled 명시값. None 이면 명시값을 지워 상속으로 되돌린다.
    public      : @전사 그룹의 read 부여/회수. None 이면 건드리지 않는다.
    """
    settings = get_settings()
    if not settings.wiki_enabled:
        raise WikiServiceError(503, "위키 기능이 비활성화되어 있습니다.")

    file = await session.get(File, file_id)
    if file is None or file.is_deleted:
        raise WikiServiceError(404, "파일을 찾을 수 없습니다.")
    if not await _can_publish(session, actor, file.id, file.user_id):
        raise WikiServiceError(404, "파일을 찾을 수 없습니다.")

    if enabled_set:
        if enabled is True and not file.is_folder:
            verdict = indexable(file)
            if not verdict.ok:
                raise WikiServiceError(
                    400, verdict.reason or "인덱싱할 수 없는 파일입니다."
                )

        before = file.wiki_enabled
        if before != enabled:
            file.wiki_enabled = enabled
            # 끈 시각을 남겨야 purger 가 유예 후 트리를 지운다. 다시 켜면 지운다.
            file.wiki_disabled_at = datetime.now(UTC) if enabled is not True else None
            _record_audit(
                session,
                actor.id,
                "wiki.toggle",
                file_id,
                {"from": before, "to": enabled, "is_folder": file.is_folder},
            )

    if public is not None:
        await _set_public(session, actor, file, public)

    await session.commit()

    if enabled_set:
        await _sync_queue(session, actor, file, enabled)

    return await resolve_wiki_state(session, file_id)


async def _sync_queue(
    session: AsyncSession, actor: User, file: File, enabled: bool | None
) -> None:
    """토글 결과를 인덱싱 큐에 반영한다.

    켜면 대상을 넣고, 끄면 대기 중인 작업을 뺀다 — 끈 파일이 잠시 뒤 인덱싱되는 일을 막는다.
    상속으로 되돌린 경우(None)는 조상 판정을 다시 해서 결정한다.
    """
    from app.services import wiki_queue

    if file.is_folder:
        scope = await folder_scope(session, actor, file)
        targets = scope.target_ids
    else:
        targets = [file.id]

    effective = await resolve_wiki_state(session, file.id)
    if effective.enabled:
        await wiki_queue.enqueue(targets)
    else:
        await wiki_queue.drop(targets)


async def _set_public(
    session: AsyncSession, actor: User, file: File, public: bool
) -> None:
    """전사 공개 = `@전사` 그룹의 read 부여/회수. 권한 서비스를 그대로 호출한다.

    위키가 권한을 직접 조작하지 않는다 — 감사 로그·캐시 무효화·SSE 발행이 전부 권한
    서비스에 이미 붙어 있고, 여기서 우회하면 그 셋이 조용히 빠진다.
    """
    group_id = await get_all_users_group_id(session)
    if group_id is None:
        raise WikiServiceError(500, "@전사 시스템 그룹이 없습니다.")

    if public:
        await permissions_service.grant_permission(
            session,
            actor,
            file.id,
            group_id,
            GroupPermission.READ,
            inherit_to_children=True,
            expires_at=None,
        )
    else:
        try:
            await permissions_service.revoke_permission(session, actor, file.id, group_id)
        except permissions_service.PermissionServiceError as exc:
            # 이미 공개가 아니면 회수할 것이 없다 — 토글을 끄는 요청에서는 성공으로 본다.
            if exc.status_code != 404:
                raise


@dataclass(frozen=True)
class WikiOverview:
    """파일 하나의 위키 화면 상태 — 토글 UI 가 필요한 값을 한 번에 담는다."""

    file: File
    state: WikiState
    public: bool
    verdict: IndexableVerdict
    status: str | None
    indexed_version: int | None
    scope: FolderScope | None


async def get_overview(
    session: AsyncSession, actor: User, file_id: int
) -> WikiOverview:
    """토글 UI 용 상태 조회. 발행 권한이 없으면 존재를 숨겨 404 로 통일한다."""
    file = await session.get(File, file_id)
    if file is None or file.is_deleted:
        raise WikiServiceError(404, "파일을 찾을 수 없습니다.")
    if not await _can_publish(session, actor, file.id, file.user_id):
        raise WikiServiceError(404, "파일을 찾을 수 없습니다.")

    doc = await get_document(session, file_id)
    return WikiOverview(
        file=file,
        state=await resolve_wiki_state(session, file_id),
        public=await is_public(session, file_id),
        verdict=indexable(file),
        status=doc.status if doc else None,
        indexed_version=doc.version if doc else None,
        scope=await folder_scope(session, actor, file) if file.is_folder else None,
    )


async def get_document(session: AsyncSession, file_id: int) -> Any | None:
    """파일의 인덱싱 레코드(wiki_documents). 없으면 None."""
    from app.models import WikiDocument

    return (
        await session.execute(
            select(WikiDocument).where(WikiDocument.file_id == file_id)
        )
    ).scalars().first()


@dataclass(frozen=True)
class DocumentItem:
    file_id: int
    name: str
    owner_display_name: str
    status: str
    version: int
    indexed_at: datetime | None
    node_count: int | None


async def list_documents(
    session: AsyncSession, user: User, page: int, size: int
) -> tuple[list[DocumentItem], int]:
    """이 사용자가 접근할 수 있는, 인덱싱된 문서 목록.

    **권한 필터를 질의 대상 선정 단계에 건다** — 목록을 만든 뒤 거르지 않는다.

    후보를 모두 가져와 `get_access_level`(Redis 캐시)로 거르고 메모리에서 페이지를 자른다.
    판정이 조상 경로를 타는 재귀 CTE 라 SQL 한 방으로 접근 가능 집합을 만들려면 판정 로직을
    두 벌 유지하게 되고, 그 순간 목록과 실제 접근이 어긋날 수 있다. 문서 수가 커지면 SQL 쪽
    필터로 옮겨야 하는 지점이며, 그때도 판정은 한 곳에서만 하도록 옮겨야 한다.
    """
    from app.models import WikiDocument

    rows = (
        await session.execute(
            select(WikiDocument, File, User.display_name)
            .join(File, File.id == WikiDocument.file_id)
            .join(User, User.id == File.user_id)
            .where(File.is_deleted.is_(False))
            .order_by(File.name.asc(), File.id.asc())
        )
    ).all()

    visible: list[DocumentItem] = []
    for doc, file, owner_name in rows:
        if file.user_id != user.id:
            level = await permissions_service.get_access_level(session, user, file)
            if level is None:
                continue
        visible.append(
            DocumentItem(
                file_id=file.id,
                name=file.name,
                owner_display_name=owner_name,
                status=doc.status,
                version=doc.version,
                indexed_at=doc.indexed_at,
                node_count=_count_nodes(doc.tree),
            )
        )

    offset = (page - 1) * size
    return visible[offset : offset + size], len(visible)


def _count_nodes(tree: dict[str, Any] | None) -> int | None:
    """트리의 노드 수 (목록 표시용). 구조가 예상과 다르면 세지 않는다."""
    if not tree:
        return None
    structure = tree.get("structure")
    if not isinstance(structure, list):
        return None

    def walk(nodes: list[Any]) -> int:
        total = 0
        for n in nodes:
            if not isinstance(n, dict):
                continue
            total += 1
            children = n.get("nodes")
            if isinstance(children, list):
                total += walk(children)
        return total

    return walk(structure)


async def get_all_users_group_id(session: AsyncSession) -> int | None:
    """`@전사` 시스템 그룹 id. 없으면 None (셋업 전이거나 마이그레이션 미적용)."""
    from app.models import Group

    return (
        await session.execute(
            select(Group.id).where(Group.is_system.is_(True), Group.is_active.is_(True))
        )
    ).scalars().first()


async def is_public(session: AsyncSession, file_id: int) -> bool:
    """이 파일에 `@전사` read 직접 부여가 있는가 (상속은 보지 않는다 — 토글 상태 표시용)."""
    from app.models import FileGroupPermission

    group_id = await get_all_users_group_id(session)
    if group_id is None:
        return False
    row = (
        await session.execute(
            select(func.count())
            .select_from(FileGroupPermission)
            .where(
                FileGroupPermission.file_id == file_id,
                FileGroupPermission.group_id == group_id,
            )
        )
    ).scalar_one()
    return row > 0


__all__ = [
    "INDEXABLE_EXTENSIONS",
    "FolderScope",
    "IndexableVerdict",
    "WikiServiceError",
    "WikiState",
    "folder_scope",
    "get_all_users_group_id",
    "indexable",
    "is_public",
    "resolve_wiki_state",
    "set_wiki",
]
