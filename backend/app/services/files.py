"""파일/폴더 서비스 (PRD 3.2, 5.2/5.3/5.10, 6.2).

권한 검사는 `ensure_file_access` 한 곳에 모은다 — Phase 1 은 소유자(user_id)만 접근하고,
그룹 권한(Phase 3)은 이 함수만 확장하면 된다.

오브젝트 키 규약 (PRD 2.1 버킷 구조):
  - 현재 버전 원본:  users/{userId}/{fileId}      → files.file_key
  - 버전 스냅샷:     versions/{fileId}/v{n}       → file_versions.object_key (v2+)

Phase 1 결정: v1 스냅샷은 별도 복사본을 만들지 않고 file_versions.object_key 가 현재 원본
키(users/{uid}/{fileId})를 그대로 가리킨다. 업로드 시 중복 저장을 피하기 위함이며, 실제 버전
스냅샷 복사는 새 버전 업로드가 생기는 Phase 2 에서 도입한다. 영구 삭제 시 키를 중복 제거해
같은 오브젝트를 두 번 지우지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import Select, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import observe_download_bytes
from app.models import File, FileGroupPermission, FileVersion, Group, User
from app.services import file_events as file_events_service
from app.services import permissions as permissions_service
from app.services import previews as previews_service
from app.services import thumbnails as thumbnails_service
from app.services.groups import get_user_group_ids, system_group_ids
from app.services.previews import PreviewPlan
from app.services.storage import StorageService

_log = get_logger("app.files")

AccessNeed = Literal["read", "write", "manage"]

# 파일 크기 상한 10 GB (PRD 1.4). nginx client_max_body_size 와 이중 방어.
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024


class FileServiceError(Exception):
    """파일 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- 오브젝트 키 규약 --------------------------------------------------------


def build_file_key(user_id: int, file_id: int) -> str:
    """현재 버전 원본 키 (PRD 2.1): users/{userId}/{fileId}."""
    return f"users/{user_id}/{file_id}"


def build_version_key(file_id: int, version: int) -> str:
    """버전 스냅샷 키 (PRD 2.1): versions/{fileId}/v{n}. (Phase 2 신규 버전용)."""
    return f"versions/{file_id}/v{version}"


# --- 권한 검사 (단일 관문) ---------------------------------------------------


async def ensure_file_access(
    session: AsyncSession, user: User, file: File | None, need: AccessNeed = "read"
) -> File:
    """파일 접근 권한을 검사하는 단일 관문. 통과하면 file 을 반환한다.

    판정 순서 (PRD 3.1.3, 5.7):
      1) 생성자(user_id)면 전권(전 need 통과).
      2) 아니면 그룹 권한을 조회 시 판정(조상 상속 포함, 캐시)해 need 를 충족하는지 본다.
    시스템 admin(users.role)은 파일 내용 접근 권한이 없다(PRD 3.6.4) — 여기서 특별대우 없음.
    존재하지 않거나 접근 불가면 404 로 통일해 리소스 존재 여부 노출을 막는다.
    """
    if file is None:
        raise FileServiceError(404, "파일을 찾을 수 없습니다.")
    if file.user_id == user.id:
        return file
    level = await permissions_service.get_access_level(session, user, file)
    if level is None or not permissions_service.permission_covers(level, need):
        raise FileServiceError(404, "파일을 찾을 수 없습니다.")
    return file


# --- 조회 -------------------------------------------------------------------


async def get_root_folder(session: AsyncSession, user_id: int) -> File:
    """사용자 루트 폴더 행(parent_folder_id IS NULL, is_folder). 승인 시 생성되어 있어야 한다."""
    row = (
        await session.execute(
            select(File).where(
                File.user_id == user_id,
                File.parent_folder_id.is_(None),
                File.is_folder.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise FileServiceError(500, "사용자 루트 폴더가 없습니다.")
    return row


async def get_file(session: AsyncSession, file_id: int) -> File | None:
    return await session.get(File, file_id)


async def folder_name_chains(
    session: AsyncSession, folder_ids: Iterable[int]
) -> dict[int, list[str]]:
    """폴더 id -> 루트 **바로 아래**부터 그 폴더까지의 폴더명 목록.

    루트 폴더(parent 가 없는 것)의 이름은 넣지 않는다 — 저장된 이름은 'root' 이고, 그 자리를
    뭐라 부를지는 화면마다 다르다(내 드라이브 / 소유자의 드라이브 …). 접두사를 붙이는 것은
    부르는 쪽 몫이다.

    부모 체인은 필요한 폴더만 레벨당 IN 1회로 모아 조회하므로 N+1 이 없다.
    """
    ids = set(folder_ids)  # 제너레이터로 와도 두 번 돈다(조회 / 체인 조립)
    cache: dict[int, tuple[str, int | None]] = {}  # folder_id -> (name, parent_id)
    need = set(ids)
    while need:
        rows = (
            await session.execute(
                select(File.id, File.name, File.parent_folder_id).where(
                    File.id.in_(need)
                )
            )
        ).all()
        for fid, name, pid in rows:
            cache[fid] = (name, pid)
        need = {
            pid
            for (_, pid) in cache.values()
            if pid is not None and pid not in cache
        }

    chains: dict[int, list[str]] = {}
    for start in ids:
        names: list[str] = []
        pid: int | None = start
        while pid is not None and pid in cache:
            name, parent = cache[pid]
            if parent is None:  # 루트 폴더 도달 — 실제 이름은 넣지 않는다
                break
            names.append(name)
            pid = parent
        names.reverse()
        chains[start] = names
    return chains


async def annotate_location(
    session: AsyncSession, user: User, files: list[File]
) -> None:
    """각 파일에 조상 폴더 경로 문자열 `location` 을 in-place 부착한다(최근·즐겨찾기 위치 표기용).

    경로는 최상위부터 직속 부모까지 폴더명을 " / " 로 이은 것이다. 루트 폴더(name='root')는
    실제 이름 대신 소유 여부에 따라 "내 드라이브"(내 파일) 또는 "내 드라이브 / 공유"(타인 공유)로
    대체한다 — 통합 드라이브에서 "공유됨"이 내 드라이브 아래 가상 "공유" 폴더로 합쳐지므로 위치
    문자열도 이에 맞춘다.
    """
    chains = await folder_name_chains(
        session, {f.parent_folder_id for f in files if f.parent_folder_id is not None}
    )

    for f in files:
        # 루트 직속이면 부모가 없다 — 접두사만 남는다.
        names = [] if f.parent_folder_id is None else chains[f.parent_folder_id]
        prefix = "내 드라이브" if f.user_id == user.id else "내 드라이브 / 공유"
        f.location = " / ".join([prefix, *names])  # type: ignore[attr-defined]


# 대상 노드에서 루트까지의 조상 체인. depth 0 이 대상 자신이고 마지막이 루트다.
_TRAIL_SQL = text(
    """
    WITH RECURSIVE trail AS (
        SELECT id, parent_folder_id, name, user_id, 0 AS depth
        FROM files WHERE id = :file_id
        UNION ALL
        SELECT f.id, f.parent_folder_id, f.name, f.user_id, t.depth + 1
        FROM files f JOIN trail t ON f.id = t.parent_folder_id
    )
    SELECT id, parent_folder_id, name, user_id, depth FROM trail ORDER BY depth ASC
    """
)


@dataclass(frozen=True)
class Crumb:
    id: int
    name: str


@dataclass(frozen=True)
class Breadcrumb:
    """폴더 하나를 URL 로 열었을 때 복원할 경로.

    crumbs 는 루트 **바로 아래**부터 대상 폴더 자신까지다. 루트 자리를 뭐라 부를지는
    부르는 쪽 몫이라 넣지 않는다(folder_name_chains 와 같은 규약) — 화면은 여기에
    "내 드라이브"(shared=False) 또는 "내 드라이브 / 공유"(shared=True)를 앞에 붙인다.
    """

    crumbs: list[Crumb]
    shared: bool


async def folder_breadcrumb(
    session: AsyncSession, user: User, file_id: int
) -> Breadcrumb:
    """URL 로 진입한 폴더의 breadcrumb 를 복원한다.

    **열 수 있는 조상만 담는다.** 공유로 닿은 항목은 권한을 준 지점 위쪽이 남의 드라이브라
    크럼을 눌러도 404 다 — 그 위는 잘라내고 shared=True 로 알린다. 화면은 그 자리에 "공유"
    가상 폴더를 놓아 목록으로 돌아갈 길을 만든다.

    대상부터 위로 한 칸씩 접근을 확인한다. **소유는 위로 연속이 아니다** — 남의 공유 폴더
    안에 내가 만든 폴더는 내 것이지만 그 부모는 남의 것이다. 소유자라고 전체 체인을
    내주면 그 경우 열리지 않는 크럼을 보여주게 된다.

    조회당 조상 수만큼 권한 판정이 도는데, 이 API 는 딥링크·새로고침에서만 불린다 —
    앱 안에서 오갈 때는 화면이 history 에 실어둔 경로를 쓴다.
    """
    node = await ensure_file_access(session, user, await get_file(session, file_id))

    rows = (await session.execute(_TRAIL_SQL, {"file_id": node.id})).all()
    # 루트 행(parent 없음)은 크럼에서 뺀다 — 그 자리 이름은 화면이 정한다.
    chain = [r for r in reversed(rows) if r.parent_folder_id is not None]

    start = len(chain)  # 열 수 있는 구간의 시작. 아래 루프가 위로 밀어 올린다.
    for i in range(len(chain) - 1, -1, -1):
        row = chain[i]
        if row.user_id != user.id:
            ancestor = await get_file(session, row.id)
            if ancestor is None:
                break
            if await permissions_service.get_access_level(session, user, ancestor) is None:
                break  # 여기서부터 위는 남의 드라이브다.
        start = i

    # 루트까지 온전히 이어졌더라도 그 루트가 내 것이 아니면 내 드라이브 경로가 아니다.
    root_owner = rows[-1].user_id if rows else user.id
    shared = start > 0 or root_owner != user.id
    return Breadcrumb([Crumb(r.id, r.name) for r in chain[start:]], shared=shared)


async def annotate_owner_names(
    session: AsyncSession, files: Sequence[File]
) -> None:
    """각 파일에 소유자 표시명 `owner_name` 을 in-place 부착한다. 소유자 id 를 배치로 한 번에 조회.

    소유자 표시명은 목록/공유 응답 공통으로 필요하므로 별도 헬퍼로 분리한다.
    """
    if not files:
        return
    owner_ids = {f.user_id for f in files}
    rows = (
        await session.execute(
            select(User.id, User.display_name).where(User.id.in_(owner_ids))
        )
    ).all()
    name_by_id = {uid: name for uid, name in rows}
    for f in files:
        f.owner_name = name_by_id.get(f.user_id, "")  # type: ignore[attr-defined]


async def _annotate_wiki_status(
    session: AsyncSession, files: Sequence[File]
) -> None:
    """목록 배지용 위키 상태를 배치로 부착한다 (spec/wiki-index.md).

    행이 없으면 "off" 다 — 백엔드가 null 을 주지 않으므로 프런트가 분기를 빠뜨리지 않는다.
    LEFT JOIN 하나면 되므로 항목당 조회(N+1)를 만들지 않는다.
    """
    from app.models import WikiDocument

    rows = (
        await session.execute(
            select(WikiDocument.file_id, WikiDocument.status).where(
                WikiDocument.file_id.in_([f.id for f in files])
            )
        )
    ).all()
    by_file = dict(rows)
    for f in files:
        f.wiki_status = by_file.get(f.id, "off")  # type: ignore[attr-defined]


async def annotate_listing_meta(
    session: AsyncSession, user: User, files: Sequence[File]
) -> None:
    """각 파일에 소유자/그룹/권한 파생 필드를 in-place 부착한다(통합 드라이브 목록 컬럼용).

    부착 필드 (schemas.files.FileResponse):
      - owner_name:  파일 소유자(files.user_id)의 표시명.
      - permission:  요청자의 유효 권한 — 소유자면 "owner", 아니면 그룹 수준(read/write/manage).
      - group_names: 소유 항목이면 이 파일에 직접 부여된(미만료) 그룹명들(내가 공유한 대상),
                     공유받은 항목이면 접근을 부여한 그룹명들(직접 부여 우선, 없으면 상속 소스).

    효율(핫패스 N+1 회피): 소유자명 1 배치, 파일들의 직접 부여 1 배치로 흔한 경로를 끝낸다.
    상속으로만 접근하는 드문 항목만 항목당 상속 판정(get_effective_grant)으로 폴백한다.
    """
    if not files:
        return

    await annotate_owner_names(session, files)
    await _annotate_wiki_status(session, files)

    # 파일들의 직접 부여(미만료)를 그룹명과 함께 한 번에 모은다 — 소유자의 "공유 대상" 표기와
    # 공유받은 항목의 직접 부여 매칭에 공용으로 쓴다. 그룹명순으로 안정 정렬.
    now = datetime.now(UTC)
    file_ids = [f.id for f in files]
    grant_rows = (
        await session.execute(
            select(
                FileGroupPermission.file_id,
                FileGroupPermission.group_id,
                FileGroupPermission.permission,
                Group.name,
            )
            .join(Group, Group.id == FileGroupPermission.group_id)
            .where(
                FileGroupPermission.file_id.in_(file_ids),
                (FileGroupPermission.expires_at.is_(None))
                | (FileGroupPermission.expires_at > now),
            )
            .order_by(Group.name.asc())
        )
    ).all()
    directs_by_file: dict[int, list[tuple[int, str, str]]] = {}
    for fid, gid, perm, gname in grant_rows:
        directs_by_file.setdefault(fid, []).append((gid, perm, gname))

    # 공유받은 항목의 직접 부여 매칭에 쓸 내 활성 그룹 id (한 번만 조회).
    #
    # **시스템 그룹(`@전사`)은 여기서 뺀다** — 위키를 켜면 그 파일에 `@전사 read` 가 걸리는데,
    # 그걸 '나에게 접근을 준 그룹'으로 세면 두 가지가 어긋난다(spec/wiki-index.md 「프런트」):
    #   1) 그룹 칼럼이 "@전사" 를 말한다 — 이 파일을 나에게 공유한 것은 그 그룹이 아니고,
    #      전 직원에게 열려 있다는 사실은 문서 카탈로그가 말할 몫이다.
    #   2) read 짜리 부여가 **상속된 더 높은 권한을 가린다** — write 로 공유받은 폴더 안의
    #      위키 문서가 목록에서 읽기 전용으로 보인다(API 는 쓰기를 허용하므로 화면만 어긋난다).
    # 직접 부여 매칭과 상속 폴백이 **같은 목록**을 봐야 한다 — 한쪽만 걸러내면 폴백이 방금
    # 걸러낸 행을 다시 집어온다(`get_effective_grant` 는 depth 0 = 파일 자신을 포함한다).
    system_gids = await system_group_ids(session)
    user_group_ids = [
        gid
        for gid in await get_user_group_ids(session, user.id)
        if gid not in system_gids
    ]
    user_group_id_set = set(user_group_ids)

    # 내가 소유한 폴더 하위의 타인 항목 — 소유 경로로 전권을 갖는다(ensure_file_access 와 같은
    # 기준). 목록에서 항목당 판정하지 않도록 배치로 한 번에 모은다.
    ancestor_owned = await permissions_service.ancestor_owned_file_ids(
        session, user.id, [f.id for f in files if f.user_id != user.id]
    )

    for f in files:
        directs = directs_by_file.get(f.id, [])
        if f.user_id == user.id:
            # 소유 항목 — 전권. group_names 는 내가 공유한 그룹들(직접 부여 그룹명).
            # (file,group) 는 UNIQUE 이므로 한 파일의 그룹명은 서로 다르다 — 중복 제거 불필요.
            f.permission = "owner"  # type: ignore[attr-defined]
            f.group_names = [gname for _, _, gname in directs]  # type: ignore[attr-defined]
            continue
        if f.id in ancestor_owned:
            # 내 폴더 안에 협업자가 만든 항목 — 소유 경로로 전권. 소유자 컬럼에는 실제 소유자가
            # 그대로 표시되므로 "owner" 가 아니라 "manage" 로 구분해 표기한다. group_names 는
            # 같은 폴더의 내 항목들과 일관되게 이 항목에 직접 부여된 그룹으로 채운다.
            f.permission = "manage"  # type: ignore[attr-defined]
            f.group_names = [gname for _, _, gname in directs]  # type: ignore[attr-defined]
            continue
        # 공유받은 항목 — 파일 자체의 직접 부여(내 그룹) 우선. `@전사` 는 위에서 이미 빠졌다.
        level, names = permissions_service.select_direct_grant(directs, user_group_id_set)
        if level is None:
            # 직접 부여가 없으면 상속으로 접근하는 항목 — 항목당 상속 판정으로 폴백.
            level, names = await permissions_service.get_effective_grant(
                session, user, f, group_ids=user_group_ids
            )
        f.permission = level.value if level is not None else "read"  # type: ignore[attr-defined]
        f.group_names = names  # type: ignore[attr-defined]


async def _resolve_parent(
    session: AsyncSession, user: User, parent_id: int | None, need: AccessNeed = "read"
) -> File:
    """parent_id 를 대상 부모 폴더로 해석한다. None 이면 루트 폴더.

    부모는 접근 권한(need)을 만족해야 하고, 폴더이며, 삭제되지 않았어야 한다. 그룹 write 권한자가
    공유 폴더에 항목을 만들 수 있도록, 생성(폴더/업로드)은 need="write" 로 부모를 해석한다.
    """
    if parent_id is None:
        return await get_root_folder(session, user.id)

    parent = await ensure_file_access(session, user, await get_file(session, parent_id), need)
    if not parent.is_folder:
        raise FileServiceError(400, "부모가 폴더가 아닙니다.")
    if parent.is_deleted:
        raise FileServiceError(409, "삭제된 폴더에는 항목을 만들 수 없습니다.")
    return parent


def _child_listing_query(parent_id: int, folders_only: bool = False) -> Select:
    """활성 하위 항목 — 폴더 우선, 이름순 (PRD 6.2)."""
    query = select(File).where(
        File.parent_folder_id == parent_id, File.is_deleted.is_(False)
    )
    if folders_only:
        query = query.where(File.is_folder.is_(True))
    return query.order_by(File.is_folder.desc(), File.name.asc())


async def list_children(
    session: AsyncSession,
    user: User,
    parent_id: int | None,
    page: int,
    size: int,
    folders_only: bool = False,
) -> tuple[list[File], int]:
    """폴더 내 항목 목록 + 총 개수 (페이지네이션).

    folders_only 는 이동 대상 폴더 선택기용이다 — 파일이 수백 개인 폴더에서도 하위 폴더가
    페이지 밖으로 밀려나지 않게 한다.
    """
    parent = await _resolve_parent(session, user, parent_id)

    count_query = (
        select(func.count())
        .select_from(File)
        .where(File.parent_folder_id == parent.id, File.is_deleted.is_(False))
    )
    if folders_only:
        count_query = count_query.where(File.is_folder.is_(True))
    total = (await session.execute(count_query)).scalar_one()

    offset = (page - 1) * size
    rows = (
        await session.execute(
            _child_listing_query(parent.id, folders_only).offset(offset).limit(size)
        )
    ).scalars().all()
    return list(rows), total


# --- 할당량 원자적 갱신 (PRD 5.10) -------------------------------------------


async def _reserve_quota(session: AsyncSession, user_id: int, size: int) -> bool:
    """storage_used 를 원자적으로 선점한다. 초과 시 False (0 rows).

    애플리케이션에서 읽고 비교하지 않고 DB 레벨 조건부 UPDATE 로 레이스를 차단한다.
    같은 트랜잭션에 있으므로 이후 rollback 시 선점도 함께 취소된다.
    """
    result = await session.execute(
        text(
            "UPDATE users SET storage_used = storage_used + :size "
            "WHERE id = :uid AND storage_used + :size <= max_storage "
            "RETURNING id"
        ),
        {"size": size, "uid": user_id},
    )
    return result.first() is not None


async def _release_quota(session: AsyncSession, user_id: int, size: int) -> None:
    """storage_used 를 감소시킨다(음수 방지). 영구 삭제 시 사용."""
    await session.execute(
        text(
            "UPDATE users SET storage_used = GREATEST(0, storage_used - :size) "
            "WHERE id = :uid"
        ),
        {"size": size, "uid": user_id},
    )


# --- 폴더 생성 / 이름 변경 ---------------------------------------------------


async def create_folder(
    session: AsyncSession, user: User, name: str, parent_id: int | None
) -> File:
    """폴더 생성 (PRD 6.2 POST /api/files). 같은 폴더 내 동명 시 409."""
    name = name.strip()
    if not name:
        raise FileServiceError(422, "폴더 이름이 비어 있습니다.")
    parent = await _resolve_parent(session, user, parent_id, need="write")

    folder = File(
        user_id=user.id,
        group_id=None,
        parent_folder_id=parent.id,
        name=name,
        file_key="",  # 폴더는 오브젝트 키가 없다.
        mime_type=None,
        size=0,
        is_folder=True,
    )
    session.add(folder)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise FileServiceError(409, "같은 이름의 항목이 이미 있습니다.") from exc
    await session.refresh(folder)
    await file_events_service.publish_file_event(
        type="folder",
        file_id=folder.id,
        parent_folder_id=parent.id,
        actor_id=user.id,
        name=folder.name,
    )
    return folder


async def rename_file(
    session: AsyncSession, user: User, file_id: int, new_name: str
) -> File:
    """이름 변경 (PRD 6.2 PUT /api/files/{id}). 동명 충돌 시 409."""
    new_name = new_name.strip()
    if not new_name:
        raise FileServiceError(422, "이름이 비어 있습니다.")

    file = await ensure_file_access(session, user, await get_file(session, file_id), need="write")
    if file.is_deleted:
        raise FileServiceError(409, "삭제된 항목은 이름을 변경할 수 없습니다.")
    if file.parent_folder_id is None:
        raise FileServiceError(400, "루트 폴더는 이름을 변경할 수 없습니다.")

    file.name = new_name
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise FileServiceError(409, "같은 이름의 항목이 이미 있습니다.") from exc
    await session.refresh(file)
    await file_events_service.publish_file_event(
        type="rename",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=user.id,
        name=file.name,
    )
    # 확장자가 바뀌면 색인 대상 여부가 뒤집힌다 — .md → .txt 면 질의에서 빠져야 하고
    # 반대면 들어와야 한다.
    await sync_wiki(session, file)
    return file


# --- 이동 (PRD 6.2) ---------------------------------------------------------

# 대상 폴더의 조상 경로에 이동 대상이 있는지. 폴더를 자기 자신이나 자기 자손 아래로 넣으면
# 트리에서 떨어져 나간 순환이 생기므로(부모를 따라 올라가도 루트에 닿지 못한다) 미리 막는다.
# depth 0(대상 폴더 자신)도 포함하므로 "자기 안으로 이동"도 이 한 쿼리로 걸린다.
_IS_SELF_OR_DESCENDANT_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id FROM files WHERE id = :target_id
        UNION ALL
        SELECT f.id, f.parent_folder_id
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT 1 FROM ancestors WHERE id = :moving_id LIMIT 1
    """
)


async def move_file(
    session: AsyncSession, user: User, file_id: int, target_parent_id: int | None
) -> File:
    """다른 폴더로 이동 (PRD 6.2 POST /api/files/{id}/move). 대상 위치 동명 충돌 시 409.

    출발지와 목적지 **양쪽에 write** 가 필요하다 — 이동은 목적지에서 보면 생성이고 출발지에서
    보면 제거라서, 한쪽 권한만으로 허용하면 읽기 전용 폴더의 내용을 빼돌리거나 남의 폴더에
    항목을 밀어 넣을 수 있다.

    이동으로 조상 경로가 바뀌면 상속 권한 판정 결과도 바뀐다. 판정 자체는 조회 시점에 하지만
    사용자별 캐시가 남아 있으므로 양쪽 경로의 이해관계자 캐시를 무효화한다(PRD 1.4).
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="write")
    if file.parent_folder_id is None:
        raise FileServiceError(400, "루트 폴더는 이동할 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(409, "삭제된 항목은 이동할 수 없습니다.")

    source_parent_id = file.parent_folder_id
    target = await _resolve_parent(session, user, target_parent_id, need="write")
    if target.id == source_parent_id:
        return file  # 이미 그 폴더에 있다 — 조작 없이 성공으로 취급한다.

    if file.is_folder:
        cycle = (
            await session.execute(
                _IS_SELF_OR_DESCENDANT_SQL,
                {"target_id": target.id, "moving_id": file.id},
            )
        ).first()
        if cycle is not None:
            raise FileServiceError(400, "폴더를 자기 자신이나 하위 폴더로 이동할 수 없습니다.")

    # 출발지에서의 제거 권한 — 대상 항목 write 만으로는 부족하다(위 docstring 참고).
    await _resolve_parent(session, user, source_parent_id, need="write")

    file.parent_folder_id = target.id
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise FileServiceError(409, "이동할 위치에 같은 이름의 항목이 있습니다.") from exc
    await session.refresh(file)

    await permissions_service.invalidate_path_stakeholders(
        session, [source_parent_id, target.id]
    )
    # 출발지·목적지 두 폴더 모두 목록이 바뀌므로 각각 발행한다 — 한쪽만 보내면 다른 쪽을 보고
    # 있는 클라이언트가 갱신되지 않는다.
    for container_id in (source_parent_id, target.id):
        await file_events_service.publish_file_event(
            type="move",
            file_id=file.id,
            parent_folder_id=container_id,
            actor_id=user.id,
            name=file.name,
        )
    # 위키가 켜진 폴더로 옮겨 오면 색인 대상이 되고, 꺼진 폴더로 나가면 질의에서 빠진다 —
    # 상속 판정 결과가 곧 정책이다.
    await sync_wiki(session, file)
    return file


# --- 업로드 (스트리밍) -------------------------------------------------------


def _upload_size(upload: UploadFile) -> int:
    """UploadFile 의 바이트 크기. 전체 메모리 적재 없이 구한다.

    multipart 파서가 채운 .size 를 우선 쓰고, 없으면 디스크 스풀 파일을 seek 해 구한다.
    """
    if upload.size is not None:
        return upload.size
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(0)
    return size


async def upload_file(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    upload: UploadFile,
    parent_id: int | None,
) -> File:
    """스트리밍 업로드 (PRD 3.2, 5.10).

    흐름: 할당량 원자적 선점 → files 행 생성(current_version=1) → MinIO put →
    file_versions v1 기록 → commit. 실패 시 트랜잭션 롤백으로 할당량 선점까지 취소하고,
    기록됐을 수 있는 MinIO 오브젝트는 best-effort 로 정리한다.
    """
    filename = (upload.filename or "untitled").strip() or "untitled"
    size = _upload_size(upload)
    if size > MAX_FILE_SIZE:
        raise FileServiceError(413, "파일 크기가 상한(10GB)을 초과했습니다.")

    parent = await _resolve_parent(session, user, parent_id, need="write")

    # 1) 할당량 원자적 선점 (같은 트랜잭션 — 실패 시 롤백으로 함께 취소).
    if not await _reserve_quota(session, user.id, size):
        await session.rollback()
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    # 2) files 행 생성 — id 를 얻어 키를 확정한다.
    mime = upload.content_type or "application/octet-stream"
    file = File(
        user_id=user.id,
        group_id=None,
        parent_folder_id=parent.id,
        name=filename,
        file_key="",
        mime_type=mime,
        size=size,
        is_folder=False,
        current_version=1,
    )
    session.add(file)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise FileServiceError(409, "같은 이름의 파일이 이미 있습니다.") from exc

    file_key = build_file_key(user.id, file.id)
    file.file_key = file_key
    # v1 스냅샷은 현재 원본 키를 재사용한다 (모듈 docstring 참조).
    session.add(
        FileVersion(
            file_id=file.id,
            version=1,
            object_key=file_key,
            size=size,
            mime_type=mime,
            uploaded_by=user.id,
        )
    )
    await session.flush()

    # 3) MinIO 스트리밍 저장 — 실패 시 트랜잭션/오브젝트 롤백.
    await upload.seek(0)
    try:
        await storage.put_async(file_key, upload.file, size, mime)
    except Exception as exc:
        await session.rollback()
        await _safe_delete_object(storage, file_key)
        raise FileServiceError(502, "오브젝트 스토리지 저장에 실패했습니다.") from exc

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await _safe_delete_object(storage, file_key)
        raise FileServiceError(409, "같은 이름의 파일이 이미 있습니다.") from exc

    await session.refresh(file)
    # 이미지면 썸네일 생성 (best-effort, 실패해도 업로드는 성공 상태 유지) — PRD 3.2.
    await thumbnails_service.maybe_generate(session, storage, file)
    # maybe_generate 는 실패 시 내부에서 rollback 한다(SVG·깨진 이미지 등). 그러면 세션의
    # 인스턴스가 전부 expire 되므로, 이어서 읽는 것들을 되살린다 — 이게 없으면 아래 user.id 가
    # MissingGreenlet 을 던져 "업로드는 성공 상태 유지"라는 약속이 깨지고 500 이 된다.
    await revive(session, user)
    await revive(session, file)
    await sync_wiki(session, file)
    await file_events_service.publish_file_event(
        type="upload",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=user.id,
        name=file.name,
    )
    return file


async def _safe_delete_object(storage: StorageService, key: str) -> None:
    try:
        await storage.delete_async(key)
    except Exception:  # noqa: BLE001 - best-effort 정리, 실패해도 무시
        pass


# --- 배치 업로드 (폴더 업로드) ----------------------------------------------
#
# 한 요청에 여러 파일 + 각자의 상대 경로를 받아 폴더 트리를 만들며 저장한다.
# 파일 저장 자체는 upload_file 을 그대로 재사용한다 — upload_file 이 **파일마다 독립적으로
# commit** 하므로(위 함수 참조) 한 파일의 실패/rollback 이 이미 커밋된 앞선 파일에 영향을
# 주지 않는다. 덕분에 할당량 선점·MinIO 스트리밍·v1 버전 기록·썸네일·SSE 가 전부 재사용된다.
#
# 부분 성공은 예외가 아니라 정상 경로다. 200개 중 3개가 409 로 실패해도 나머지는 저장된다.


MAX_PATH_DEPTH = 32       # 경로 세그먼트 수 상한
MAX_PATH_LENGTH = 4096    # 상대 경로 문자열 길이 상한
MAX_NAME_LENGTH = 255     # 세그먼트(파일/폴더 이름) 길이 상한


def _is_control_char(ch: str) -> bool:
    return ch < "\x20" or ch == "\x7f"


def normalize_relpath(raw: str) -> list[str]:
    """상대 경로를 세그먼트 리스트로 정규화한다. **이 함수가 유일한 신뢰 경계다.**

    반환된 세그먼트는 그대로 create_folder 의 이름과 파일명이 된다. 오브젝트 키는
    build_file_key(user_id, file_id) 로 id 기반이라 사용자 경로가 스토리지 키에 들어가지
    않지만, DB 이름 컬럼에는 들어가므로 여기서 전부 걸러야 한다.

    프론트엔드 `lib/fileTree.ts` 의 검증이 이 규칙과 일치해야 한다 — 어긋나면 클라이언트가
    통과시킨 항목을 서버가 거부하는 상황이 생긴다.

    실패 시 FileServiceError(422) 를 던진다. 호출자(batch_upload)가 항목별로 잡아
    부분 실패로 기록하므로, 경로 하나가 배치 전체를 중단시키지는 않는다.
    """
    if len(raw) > MAX_PATH_LENGTH:
        raise FileServiceError(422, "경로가 너무 깁니다.")

    unified = raw.replace("\\", "/")  # Windows 클라이언트
    if unified.startswith("/"):
        raise FileServiceError(422, "절대 경로는 사용할 수 없습니다.")
    # 드라이브 문자(C:, d:) — 세그먼트 검사만으로는 걸리지 않으므로 선행 차단.
    head = unified.split("/", 1)[0]
    if len(head) >= 2 and head[1] == ":":
        raise FileServiceError(422, "절대 경로는 사용할 수 없습니다.")

    segments: list[str] = []
    for raw_seg in unified.split("/"):
        if raw_seg in ("", "."):
            continue  # 중복 슬래시·현재 디렉터리는 무시
        if raw_seg == "..":
            # 상쇄 계산을 하지 않고 즉시 거부한다 — "a/../b" 도 막는다.
            raise FileServiceError(422, "상위 경로(..)는 사용할 수 없습니다.")
        seg = raw_seg.strip()
        if not seg:
            raise FileServiceError(422, "이름이 비어 있습니다.")
        if len(seg) > MAX_NAME_LENGTH:
            raise FileServiceError(422, "이름이 255자를 넘습니다.")
        if any(_is_control_char(ch) for ch in seg):
            raise FileServiceError(422, "이름에 쓸 수 없는 문자가 있습니다.")
        segments.append(seg)

    if not segments:
        raise FileServiceError(422, "경로가 비어 있습니다.")
    if len(segments) > MAX_PATH_DEPTH:
        raise FileServiceError(422, "폴더 깊이가 32단계를 넘습니다.")
    return segments


async def revive(session: AsyncSession, instance: object) -> None:
    """rollback 으로 expire 된 ORM 인스턴스를 되살린다.

    session.rollback() 은 세션에 붙어 있는 인스턴스를 **전부** expire 시킨다. 그 뒤 속성을
    읽으면 비동기 컨텍스트 밖에서 지연 로드가 일어나 MissingGreenlet 으로 죽는다.
    rollback 이후에도 계속 쓰는 객체가 있으면 여기서 명시적으로 다시 읽어야 한다.

    이 경로로 실제 사고가 두 번 났다.
      - 배치 업로드: 한 파일이 409 로 실패한 뒤 다음 파일에서 ensure_file_access 가 user.id 를
        읽다 죽었다. 요청당 파일이 하나인 단일 업로드에서는 rollback 직후 요청이 끝나 안 보인다.
      - 썸네일 생성 실패(SVG·깨진 이미지): maybe_generate 가 내부에서 rollback 하는데 file 만
        되살려서, 호출자가 이어서 읽는 user 가 expire 상태로 남았다.
    """
    if inspect(instance).expired:
        await session.refresh(instance)


async def _lookup_child(
    session: AsyncSession, parent_id: int, name: str
) -> File | None:
    """부모 폴더 안의 활성(비삭제) 동명 항목. 없으면 None."""
    return (
        await session.execute(
            select(File).where(
                File.parent_folder_id == parent_id,
                File.name == name,
                File.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()


async def _ensure_folder_path(
    session: AsyncSession,
    user: User,
    root_id: int,
    segments: Sequence[str],
    cache: dict[tuple[int, str], int],
) -> list[int]:
    """root_id 아래로 segments 경로의 폴더를 확보하고 세그먼트별 폴더 id 를 반환한다.

    기존 폴더가 있으면 재사용한다(= 기존 트리에 병합). 같은 이름의 *파일*이 있으면 409.

    ORM 인스턴스가 아니라 int id 만 들고 다니는 게 중요하다 — 뒤이은 파일 업로드가 실패해
    session.rollback() 이 돌면 세션에 남은 ORM 객체는 expire 되고, 그 뒤 속성 접근이
    비동기 컨텍스트 밖 lazy load 를 유발한다(MissingGreenlet).
    """
    ids: list[int] = []
    parent_id = root_id
    for seg in segments:
        # 앞선 항목/세그먼트의 실패가 rollback 을 남겼을 수 있다.
        await revive(session, user)
        key = (parent_id, seg)
        cached = cache.get(key)
        if cached is not None:
            ids.append(cached)
            parent_id = cached
            continue

        existing = await _lookup_child(session, parent_id, seg)
        if existing is not None:
            if not existing.is_folder:
                raise FileServiceError(409, f"'{seg}' 은(는) 파일이라 폴더로 쓸 수 없습니다.")
            folder_id = existing.id
        else:
            try:
                folder_id = (await create_folder(session, user, seg, parent_id)).id
            except FileServiceError as exc:
                if exc.status_code != 409:
                    raise
                # 동시 생성 경합 — uq_files_sibling_name 이 한쪽을 떨궜다. 재조회해 재사용한다.
                # 이 복구가 없으면 두 배치가 같은 폴더를 만들 때 산발적으로 실패한다.
                raced = await _lookup_child(session, parent_id, seg)
                if raced is None or not raced.is_folder:
                    raise
                folder_id = raced.id

        cache[key] = folder_id
        ids.append(folder_id)
        parent_id = folder_id
    return ids


async def batch_upload(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    uploads: Sequence[UploadFile],
    paths: Sequence[str],
    dirs: Sequence[str],
    parent_id: int | None,
) -> tuple[list[dict], dict[str, int]]:
    """여러 파일을 상대 경로대로 저장한다. (항목별 결과, 경로→폴더 id 맵) 을 반환한다.

    uploads 가 비고 dirs 만 있는 요청도 유효하다 — 폴더 트리만 먼저 확정하는 용도로,
    클라이언트가 64MB 초과 파일을 단일 업로드 경로로 보낼 때 parent_id 를 얻는 데 쓴다.

    권한은 최상위 parent_id 에서 한 번만 검증한다. 그 아래 폴더는 전부 우리가 부모 id 를
    지정해 만들거나 부모 id 로 조회한 것이라 후손임이 구조적으로 보장된다.
    """
    root_id = (await _resolve_parent(session, user, parent_id, need="write")).id

    cache: dict[tuple[int, str], int] = {}
    folders: dict[str, int] = {}
    items: list[dict] = []

    async def resolve_dir(segments: Sequence[str]) -> int:
        """디렉터리 경로 → 폴더 id. 조상 경로도 folders 맵에 함께 기록한다."""
        if not segments:
            return root_id
        joined = "/".join(segments)
        hit = folders.get(joined)
        if hit is not None:
            return hit
        ids = await _ensure_folder_path(session, user, root_id, segments, cache)
        for depth, folder_id in enumerate(ids, start=1):
            folders["/".join(segments[:depth])] = folder_id
        return ids[-1]

    def record_error(path: str, exc: FileServiceError) -> None:
        items.append(
            {
                "path": path,
                "status": "error",
                "code": exc.status_code,
                "detail": exc.detail,
            }
        )

    # 1) 디렉터리 먼저 — 파일이 하나도 없는 폴더도 id 를 얻게 한다.
    for raw_dir in dirs:
        try:
            await resolve_dir(normalize_relpath(raw_dir))
        except FileServiceError as exc:
            record_error(raw_dir, exc)

    # 2) 파일 — 실패해도 다음 파일로 계속 진행한다(부분 성공).
    for upload, raw_path in zip(uploads, paths, strict=True):
        # 직전 항목이 실패하며 rollback 했다면 user 가 expire 되어 있다(_revive_user 참조).
        await revive(session, user)
        try:
            segments = normalize_relpath(raw_path)
            *dir_segments, filename = segments
            folder_id = await resolve_dir(dir_segments)
        except FileServiceError as exc:
            record_error(raw_path, exc)
            continue

        # 파일명은 정규화된 경로의 마지막 세그먼트를 쓴다. multipart 의 filename 은
        # 클라이언트가 경로 전체를 넣어 보내기도 해 신뢰하지 않는다.
        upload.filename = filename
        try:
            created = await upload_file(session, storage, user, upload, folder_id)
        except FileServiceError as exc:
            record_error(raw_path, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            # 예상 못 한 예외(썸네일 생성 경로의 버그 등)로 요청 전체가 500 이 되면, 이미
            # 저장된 앞선 파일들까지 클라이언트에서 실패로 처리된다. 배치는 부분 성공이
            # 정상 경로이므로 이 파일만 실패로 남기고 계속 진행한다.
            _log.exception("batch_item_failed", path=raw_path, error=str(exc))
            record_error(raw_path, FileServiceError(500, "파일을 저장하지 못했습니다."))
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 - 세션이 이미 끊겼을 수 있다.
                pass
            continue

        # 세션에서 떼어낸다 — 이후 파일이 실패해 rollback 이 돌면 세션에 남은 인스턴스는
        # expire 되고, 라우트에서 응답을 만들 때 속성 접근이 lazy load 를 유발한다.
        # upload_file 이 마지막에 refresh 해 둔 상태 그대로 detached 로 보존한다.
        session.expunge(created)
        items.append({"path": raw_path, "status": "created", "file": created})

    return items, folders


# --- 다운로드 (게이트웨이 모델) ---------------------------------------------


async def prepare_download(
    session: AsyncSession, storage: StorageService, user: User, file_id: int
) -> tuple[str, str, str]:
    """게이트웨이 다운로드 준비 (PRD 2.2, 6.2).

    소유자 검사 후 내부 presign(60s)을 생성해 nginx `/_minio/` 경로(X-Accel-Redirect 값)로
    변환한다. 반환: (internal_redirect_path, filename, mime_type). 폴더면 400.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id))
    if file.is_folder:
        raise FileServiceError(400, "폴더는 다운로드할 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(404, "파일을 찾을 수 없습니다.")

    presigned = await storage.presign_get_async(file.file_key)
    internal = storage.to_internal_redirect(presigned)
    mime = file.mime_type or "application/octet-stream"
    # 게이트웨이 모델상 실제 스트리밍은 nginx 가 하므로, 인가된 파일 크기를 계측한다 (PRD 11장).
    observe_download_bytes(file.size)
    return internal, file.name, mime


# --- 썸네일 / 미리보기 (PRD 3.2) --------------------------------------------


async def prepare_thumbnail(
    session: AsyncSession, storage: StorageService, user: User, file_id: int
) -> str:
    """썸네일 게이트웨이 스트리밍 준비 (PRD 3.2). read 권한 검사 후 internal redirect 반환.

    썸네일이 아직 없으면(비이미지이거나 생성 실패) 404. 인라인(image/png)으로 렌더된다.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id))
    if file.is_deleted or not file.thumbnail_key:
        raise FileServiceError(404, "썸네일을 찾을 수 없습니다.")
    presigned = await storage.presign_get_async(file.thumbnail_key)
    return storage.to_internal_redirect(presigned)


async def prepare_preview(
    session: AsyncSession, storage: StorageService, user: User, file_id: int
) -> tuple[PreviewPlan, str]:
    """미리보기 계획 준비 (PRD 3.2). read 권한 검사 후 (plan, filename) 반환.

    폴더/삭제 파일은 배제하고, 미지원 타입은 plan.kind == "unsupported" 로 표시해 라우트가 415 로
    응답하게 한다. 지원 타입은 게이트웨이 인라인(image/pdf) 또는 텍스트 head(text)로 계획된다.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id))
    if file.is_folder:
        raise FileServiceError(400, "폴더는 미리볼 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(404, "파일을 찾을 수 없습니다.")
    plan = await previews_service.build_preview_plan(storage, file)
    return plan, file.name


# --- 버전 관리 (PRD 3.3, 5.3, 6.2) ------------------------------------------
#
# 키 불변식(invariant): 어떤 파일이든 `version == current_version` 인 file_versions 행의
# object_key 는 항상 원본 키(file_key = users/{uid}/{fileId})를 가리키고, 그보다 오래된
# 버전은 스냅샷 키(versions/{fileId}/v{n})를 가리킨다. 새 버전이 생길 때(재업로드/복구)
# 직전 원본을 스냅샷으로 서버측 복사하고 직전 버전 행의 object_key 를 스냅샷 키로 갱신한다.
#
# 할당량: 스냅샷은 이전 바이트를 계속 점유하므로, 새 버전이 추가될 때 storage_used 는
# "새 내용 크기"만큼만 추가로 선점한다. 결과적으로 storage_used == 모든 버전 크기 합계.


def versioned_filename(name: str, version: int) -> str:
    """다운로드 파일명에 버전 표기를 넣는다: `report.pdf` → `report (v3).pdf`."""
    base, dot, ext = name.rpartition(".")
    if dot:
        return f"{base} (v{version}).{ext}"
    return f"{name} (v{version})"


async def _snapshot_current_original(
    session: AsyncSession, storage: StorageService, file: File
) -> str:
    """현재 원본(file_key)을 versions/{id}/v{current} 로 서버측 복사하고, 현재 버전 행의
    object_key 를 그 스냅샷 키로 갱신한다. 반환: 생성한 스냅샷 키.

    이후 file_key 에 새 내용을 덮어써도 직전 버전 바이트는 스냅샷에 보존된다.
    """
    snapshot_key = build_version_key(file.id, file.current_version)
    await storage.copy_async(file.file_key, snapshot_key)
    await session.execute(
        update(FileVersion)
        .where(
            FileVersion.file_id == file.id,
            FileVersion.version == file.current_version,
        )
        .values(object_key=snapshot_key)
    )
    return snapshot_key


async def _commit_new_version(
    session: AsyncSession,
    storage: StorageService,
    file: File,
    *,
    size: int,
    mime: str,
    uploaded_by: int,
    write_new_content,
) -> File:
    """새 버전 생성의 공통 절차 — 재업로드/복구가 공유한다.

    호출 전 제약: 할당량 선점 완료, `file` 은 접근 검증된 활성 파일.
    `write_new_content()` 는 file_key 에 새 버전 바이트를 기록하는 async 콜러블이다
    (재업로드=스트림 put, 복구=대상 버전에서 서버측 copy).

    절차: 직전 원본 스냅샷 → DB(직전 행 object_key 갱신 + 새 버전 행 + files 갱신) flush →
    file_key 에 새 내용 기록 → commit. 실패 시 롤백 + best-effort 로 오브젝트 원상복구한다.
    """
    prev_original_key = file.file_key
    new_version = file.current_version + 1

    # 1) 직전 원본을 스냅샷으로 보존 (MinIO 서버측 복사).
    try:
        snapshot_key = await _snapshot_current_original(session, storage, file)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise FileServiceError(502, "버전 스냅샷 복사에 실패했습니다.") from exc

    # 2) DB 반영(flush 로 무결성 오류 조기 감지). new_version 행의 object_key 는 file_key 재사용.
    session.add(
        FileVersion(
            file_id=file.id,
            version=new_version,
            object_key=prev_original_key,
            size=size,
            mime_type=mime,
            uploaded_by=uploaded_by,
        )
    )
    file.base_version = file.current_version
    file.current_version = new_version
    file.size = size
    file.mime_type = mime
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        await _safe_delete_object(storage, snapshot_key)
        raise FileServiceError(409, "버전 기록에 실패했습니다.") from exc

    # 3) file_key 에 새 내용 기록 (스트림 put 또는 서버측 copy).
    try:
        await write_new_content(prev_original_key)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        await _safe_delete_object(storage, snapshot_key)
        raise FileServiceError(502, "오브젝트 스토리지 저장에 실패했습니다.") from exc

    # 4) commit. 실패 시 file_key 는 이미 새 내용이므로 스냅샷에서 원상복구를 시도한다.
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        await _safe_restore_object(storage, snapshot_key, prev_original_key)
        await _safe_delete_object(storage, snapshot_key)
        raise FileServiceError(500, "버전 저장에 실패했습니다.") from exc

    await session.refresh(file)
    # 새 버전 내용으로 썸네일 갱신 (재업로드/복구 공통, best-effort) — PRD 3.2.
    await thumbnails_service.maybe_generate(session, storage, file)
    await revive(session, file)  # 실패 시 rollback 으로 expire 된다(revive 주석 참조).
    # 재업로드/버전 복구/재개 업로드(version)가 모두 이 경로를 지난다 — 단일 발행 지점.
    await file_events_service.publish_file_event(
        type="version",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=uploaded_by,
        name=file.name,
    )
    await _enqueue_wiki_reindex(session, file)
    return file


async def _enqueue_wiki_reindex(session: AsyncSession, file: File) -> None:
    """새 버전이 올라왔음을 위키에 알린다 (spec/wiki-index.md).

    버전업 경로가 여기 하나로 모이므로(재업로드·버전 복구·재개 업로드) 훅도 한 군데면 된다.
    구 트리는 유지한 채 낡았음만 표시한다 — 재인덱싱이 끝날 때까지 그것으로 답한다.
    """
    from app.services import wiki as wiki_service

    try:
        state = await wiki_service.resolve_wiki_state(session, file.id)
        if state.enabled and wiki_service.indexable(file).ok:
            await wiki_service.mark_stale(session, file.id)
        await sync_wiki(session, file)
    except Exception:  # noqa: BLE001 - 인덱싱 예약 실패가 업로드를 되돌리면 안 된다
        pass


async def sync_wiki(session: AsyncSession, file: File) -> None:
    """파일의 위치가 정해지거나 바뀐 뒤 위키 상태를 맞춘다 (spec/wiki-index.md).

    업로드·이동·이름 변경에서 호출한다. 유효 상태를 바꾸는 축이 셋이다 — 위치(상속 경로),
    이름(확장자가 색인 대상인지), 토글 자체. 폴더 토글은 **그 시점의** 하위 파일만 큐에 넣으므로
    이후에 들어오거나 옮겨 오는 파일은 여기서 잡아야 한다.

    폴더를 넘기면 하위 전체를 훑는다 — 폴더를 옮기면 그 아래 모든 문서의 상속이 한꺼번에 바뀐다.

    지연 임포트는 순환을 피하기 위한 것이다(wiki 가 permissions → groups 를 타고 files 를 본다).
    실패해도 업로드/이동을 되돌리지 않는다 — 놓친 항목은 다음 토글·버전업에서 다시 들어온다.
    """
    from app.services import wiki as wiki_service

    try:
        await wiki_service.sync_file(session, file)
    except Exception:  # noqa: BLE001 - 색인 예약 실패가 파일 조작을 되돌리면 안 된다
        _log.warning("wiki_sync_failed", file_id=file.id)


async def _safe_restore_object(
    storage: StorageService, src_key: str, dst_key: str
) -> None:
    try:
        await storage.copy_async(src_key, dst_key)
    except Exception:  # noqa: BLE001 - best-effort 복구
        pass


async def reupload_file(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    file_id: int,
    upload: UploadFile,
    base_version: int | None,
) -> File:
    """기존 파일에 새 내용을 올려 새 버전을 만든다 (PRD 3.3).

    base_version 이 주어지고 current_version 과 불일치하면 409(충돌 감지). 미전달 시 강제 덮어쓰기.
    파일명은 유지하고 내용/크기/mime 만 갱신한다.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="write")
    if file.is_folder:
        raise FileServiceError(400, "폴더는 재업로드할 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(409, "삭제된 파일은 재업로드할 수 없습니다.")

    if base_version is not None and base_version != file.current_version:
        raise FileServiceError(
            409,
            f"버전 충돌: 최신 버전은 v{file.current_version} 입니다 (요청 base v{base_version}).",
        )

    size = _upload_size(upload)
    if size > MAX_FILE_SIZE:
        raise FileServiceError(413, "파일 크기가 상한(10GB)을 초과했습니다.")
    mime = upload.content_type or file.mime_type or "application/octet-stream"

    # 할당량 선점 — 스냅샷이 이전 바이트를 점유하므로 새 크기만큼만 추가 선점.
    if not await _reserve_quota(session, user.id, size):
        await session.rollback()
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    async def _put_stream(_prev_key: str) -> None:
        await upload.seek(0)
        await storage.put_async(file.file_key, upload.file, size, mime)

    return await _commit_new_version(
        session,
        storage,
        file,
        size=size,
        mime=mime,
        uploaded_by=user.id,
        write_new_content=_put_stream,
    )


async def list_versions(
    session: AsyncSession, user: User, file_id: int
) -> tuple[File, list[tuple[FileVersion, str]]]:
    """버전 히스토리 (PRD 6.2). (file, [(version, 업로더 표시명), ...] 최신순) 반환."""
    file = await ensure_file_access(session, user, await get_file(session, file_id))
    if file.is_folder:
        raise FileServiceError(400, "폴더는 버전이 없습니다.")

    rows = (
        await session.execute(
            select(FileVersion, User.display_name)
            .join(User, FileVersion.uploaded_by == User.id)
            .where(FileVersion.file_id == file.id)
            .order_by(FileVersion.version.desc())
        )
    ).all()
    return file, [(v, name) for v, name in rows]


async def prepare_version_download(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    file_id: int,
    version: int,
) -> tuple[str, str, str]:
    """특정 버전 게이트웨이 다운로드 준비 (PRD 6.2). 반환: (internal_redirect, filename, mime).

    파일명에 버전 표기를 넣는다(예: `report (v2).pdf`). 없는 버전이면 404.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id))
    if file.is_folder:
        raise FileServiceError(400, "폴더는 다운로드할 수 없습니다.")

    row = (
        await session.execute(
            select(FileVersion).where(
                FileVersion.file_id == file.id, FileVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise FileServiceError(404, "해당 버전을 찾을 수 없습니다.")

    presigned = await storage.presign_get_async(row.object_key)
    internal = storage.to_internal_redirect(presigned)
    mime = row.mime_type or file.mime_type or "application/octet-stream"
    observe_download_bytes(row.size)
    return internal, versioned_filename(file.name, version), mime


async def restore_version(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    file_id: int,
    version: int,
) -> File:
    """과거 버전을 새 버전으로 복사 생성한다 (PRD 3.3 — 이력 보존, 유실 0%).

    대상 버전 바이트를 file_key 로 서버측 복사해 current_version+1 을 만든다. files.size/mime 을
    대상 버전 기준으로 갱신하고 할당량에 반영한다.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="write")
    if file.is_folder:
        raise FileServiceError(400, "폴더는 복구할 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(409, "삭제된 파일은 복구할 수 없습니다.")

    target = (
        await session.execute(
            select(FileVersion).where(
                FileVersion.file_id == file.id, FileVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise FileServiceError(404, "해당 버전을 찾을 수 없습니다.")

    # 대상 버전 키를 미리 확정한다 — 스냅샷 단계에서 현재 버전 행 object_key 가 바뀌므로,
    # 대상이 곧 현재 버전이면 그 키(=file_key)를 먼저 캡처해 둔다.
    target_size = target.size
    target_mime = target.mime_type or file.mime_type or "application/octet-stream"
    target_key = target.object_key

    if not await _reserve_quota(session, user.id, target_size):
        await session.rollback()
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    async def _copy_from_target(prev_key: str) -> None:
        # 대상이 직전 현재 버전이었다면 그 바이트는 방금 스냅샷 키로 복사됐고 file_key 는
        # 아직 동일 바이트다. 어느 경우든 target_key(캡처값) 또는 file_key 에서 복사하면 된다.
        source = prev_key if target_key == prev_key else target_key
        await storage.copy_async(source, file.file_key)

    return await _commit_new_version(
        session,
        storage,
        file,
        size=target_size,
        mime=target_mime,
        uploaded_by=user.id,
        write_new_content=_copy_from_target,
    )


# --- 삭제 / 휴지통 -----------------------------------------------------------

# 대상 파일과 모든 하위 항목을 재귀로 훑는 공통 CTE (parent_folder_id 자기참조 트리).
_SUBTREE_CTE = (
    "WITH RECURSIVE sub AS ("
    "  SELECT id FROM files WHERE id = :root "
    "  UNION ALL "
    "  SELECT f.id FROM files f JOIN sub s ON f.parent_folder_id = s.id"
    ")"
)


async def soft_delete(session: AsyncSession, user: User, file_id: int) -> None:
    """소프트 삭제 (PRD 6.2). 폴더면 하위 전체를 단일 recursive CTE UPDATE 로 처리한다."""
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="write")
    if file.parent_folder_id is None:
        raise FileServiceError(400, "루트 폴더는 삭제할 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(409, "이미 휴지통에 있는 항목입니다.")

    # 발행에 쓸 값은 commit 전에 캡처한다(하위 재귀 UPDATE 후 ORM 접근 부작용 회피).
    fid, parent_id, name = file.id, file.parent_folder_id, file.name
    await session.execute(
        text(
            _SUBTREE_CTE
            + " UPDATE files SET is_deleted = TRUE, deleted_at = now() "
            "WHERE id IN (SELECT id FROM sub) AND is_deleted = FALSE"
        ),
        {"root": file.id},
    )
    await session.commit()
    await file_events_service.publish_file_event(
        type="delete",
        file_id=fid,
        parent_folder_id=parent_id,
        actor_id=user.id,
        name=name,
    )


async def list_trash(session: AsyncSession, user: User) -> list[File]:
    """휴지통 목록 — 직접 삭제된 최상위 항목만 (PRD 6.2).

    부모가 아직 살아 있는(또는 없는) 삭제 항목이 '삭제 루트'다. 폴더 재귀 삭제로 함께
    지워진 하위 항목은 부모도 삭제 상태이므로 제외된다 — 별도 플래그 없이 판별한다.
    """
    parent = File.__table__.alias("p")
    rows = (
        await session.execute(
            select(File)
            .outerjoin(parent, File.parent_folder_id == parent.c.id)
            .where(
                File.user_id == user.id,
                File.is_deleted.is_(True),
                (parent.c.id.is_(None)) | (parent.c.is_deleted.is_(False)),
            )
            .order_by(File.deleted_at.desc())
        )
    ).scalars().all()
    # 파생 필드 purge_at — 자동 영구 삭제 예정 시각. 프론트가 보존 기간을 따로 받지 않고
    # purge_at - deleted_at 으로 역산해 안내 문구를 만든다(spec/trash-retention-purge.md).
    retention = settings.trash_retention_days
    for row in rows:
        row.purge_at = (  # type: ignore[attr-defined]
            row.deleted_at + timedelta(days=retention)
            if retention > 0 and row.deleted_at is not None
            else None
        )
    return list(rows)


async def restore_trash(session: AsyncSession, user: User, file_id: int) -> File:
    """휴지통 복구 (PRD 6.2). 부모가 삭제됐으면 루트로 재부착, 동명 충돌 시 409.

    대상과 하위 전체를 재귀로 되살린다(재귀 삭제의 역연산).
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="write")
    if not file.is_deleted:
        raise FileServiceError(409, "휴지통에 있는 항목만 복구할 수 있습니다.")

    # 재부착 대상 부모 결정: 원래 부모가 사라졌으면(삭제됐으면) 루트로.
    # 부모가 바뀌면 상속 권한 판정 대상 경로도 함께 바뀐다(조회 시 판정이라 별도 갱신은 불필요).
    target_parent_id = file.parent_folder_id
    if target_parent_id is not None:
        parent = await get_file(session, target_parent_id)
        if parent is None or parent.is_deleted:
            target_parent_id = (await get_root_folder(session, user.id)).id

    # 대상 위치에서 활성 동명 충돌 사전 검사.
    conflict = (
        await session.execute(
            select(File.id).where(
                File.parent_folder_id == target_parent_id,
                File.name == file.name,
                File.is_deleted.is_(False),
                File.id != file.id,
            )
        )
    ).first()
    if conflict is not None:
        raise FileServiceError(409, "복구 위치에 같은 이름의 항목이 있습니다.")

    if target_parent_id != file.parent_folder_id:
        file.parent_folder_id = target_parent_id
        await session.flush()

    try:
        await session.execute(
            text(
                _SUBTREE_CTE
                + " UPDATE files SET is_deleted = FALSE, deleted_at = NULL "
                "WHERE id IN (SELECT id FROM sub) AND is_deleted = TRUE"
            ),
            {"root": file.id},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise FileServiceError(409, "복구 위치에 같은 이름의 항목이 있습니다.") from exc
    await session.refresh(file)
    await file_events_service.publish_file_event(
        type="restore",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=user.id,
        name=file.name,
    )
    # 휴지통에 있는 동안 위키 설정이 바뀌었을 수 있고, 부모가 사라져 루트로 재부착되면 상속
    # 경로 자체가 달라진다. 복구 시점의 판정을 다시 적용한다 — 그러지 않으면 꺼진 문서가
    # 복구와 함께 되살아나 검색된다.
    await sync_wiki(session, file)
    return file


async def permanent_delete(
    session: AsyncSession, storage: StorageService, user: User, file_id: int
) -> None:
    """영구 삭제 (PRD 6.2). 휴지통 항목만 허용. 폴더는 하위 전체 재귀.

    인가와 상태 검사만 하고 실제 삭제는 `purge_tree` 에 위임한다 — 보존 기간 자동 정리
    (spec/trash-retention-purge.md)가 같은 함수를 호출해 할당량 회수·공유 링크 선삭제·
    썸네일 제거가 두 경로에서 어긋나지 않게 한다.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="manage")
    if not file.is_deleted:
        raise FileServiceError(409, "휴지통에 있는 항목만 영구 삭제할 수 있습니다.")
    await purge_tree(session, storage, file, actor_id=user.id)


async def subtree_stats(session: AsyncSession, root_id: int) -> tuple[int, int]:
    """(하위 포함 행 수, 모든 버전 크기 합계). 삭제하지 않고 규모만 센다 — dry-run 용."""
    row = (
        await session.execute(
            text(
                _SUBTREE_CTE + " SELECT count(*) AS n FROM files WHERE id IN (SELECT id FROM sub)"
            ),
            {"root": root_id},
        )
    ).one()
    size = (
        await session.execute(
            text(
                _SUBTREE_CTE
                + " SELECT COALESCE(SUM(v.size), 0) AS total FROM file_versions v "
                "WHERE v.file_id IN (SELECT id FROM sub)"
            ),
            {"root": root_id},
        )
    ).one()
    return int(row.n), int(size.total)


async def purge_tree(
    session: AsyncSession,
    storage: StorageService,
    file: File,
    *,
    actor_id: int | None,
    publish: bool = True,
) -> tuple[int, int]:
    """휴지통 항목 하나와 하위 전체를 영구 삭제한다. **인가는 호출자 책임.**

    MinIO 오브젝트(원본+버전) 삭제 + storage_used 감소 + files 행 삭제(versions CASCADE).
    DB 를 먼저 확정하고 오브젝트는 best-effort 로 정리해 DB 를 진실 소스로 유지한다.

    actor_id 는 사용자의 수동 삭제면 그 사용자, 자동 정리면 None. publish=False 면 이벤트를
    발행하지 않는다(배치 정리의 폭주 상한 — 초과분은 소유자별 요약으로 접는다).
    반환: (삭제한 files 행 수, 회수한 바이트 합계).
    """
    # 발행에 쓸 값은 DELETE 전에 캡처한다 — 행이 사라진 뒤 ORM 속성 접근은 재조회를 유발한다
    # (soft_delete 의 같은 주석 참조).
    # (root_owner_id — 아래 size_by_owner 루프의 owner_id 와 이름이 겹치지 않게 한다.)
    root_id, parent_id, name = file.id, file.parent_folder_id, file.name
    root_owner_id = file.user_id

    # 1) 삭제할 오브젝트 키와 회수할 용량을 수집한다.
    #    storage_used 는 모든 버전 크기 합계를 반영하므로(스냅샷 포함), 회수량도 file_versions
    #    의 크기 합계로 계산한다. 원본 키(file_key)는 현재 버전 행의 object_key 와 겹치므로
    #    set 으로 중복 제거해 같은 오브젝트를 두 번 지우지 않는다.
    file_rows = (
        await session.execute(
            text(
                _SUBTREE_CTE
                + " SELECT f.file_key, f.thumbnail_key FROM files f "
                "WHERE f.id IN (SELECT id FROM sub) AND f.is_folder = FALSE"
            ),
            {"root": root_id},
        )
    ).all()
    keys: set[str] = {r.file_key for r in file_rows if r.file_key}
    # 썸네일 오브젝트도 함께 제거한다 (thumbnails/{fileId}.png). 할당량에는 포함하지 않는다.
    keys.update(r.thumbnail_key for r in file_rows if r.thumbnail_key)

    #    회수 대상 용량은 **파일 소유자별로** 집계한다 — 폴더 하위에는 협업자가 올린(소유자가
    #    다른) 파일이 섞일 수 있고, 그 바이트는 소유자의 storage_used 에 잡혀 있기 때문이다.
    #    지우는 사람 기준으로 되돌리면 실제 소유자의 사용량이 영원히 부풀어 남는다.
    version_rows = (
        await session.execute(
            text(
                _SUBTREE_CTE
                + " SELECT f.user_id AS owner_id, v.object_key, v.size FROM file_versions v "
                "JOIN files f ON f.id = v.file_id "
                "WHERE v.file_id IN (SELECT id FROM sub)"
            ),
            {"root": root_id},
        )
    ).all()
    size_by_owner: dict[int, int] = {}
    for r in version_rows:
        size_by_owner[r.owner_id] = size_by_owner.get(r.owner_id, 0) + r.size
    keys.update(r.object_key for r in version_rows if r.object_key)

    # 2) DB 확정 — 행 삭제(versions CASCADE) + storage_used 감소.
    #    shares.file_id 는 ON DELETE CASCADE 가 없어(모델/마이그레이션 변경 회피), 영구 삭제
    #    대상(하위 포함)에 걸린 공유 링크 행을 앱 레벨에서 먼저 지워 FK 위반을 방지한다.
    #    공유 링크는 이력 보존 대상이지만 원본 파일 자체가 사라지는 영구 삭제에서는 함께 소멸한다.
    await session.execute(
        text(_SUBTREE_CTE + " DELETE FROM shares WHERE file_id IN (SELECT id FROM sub)"),
        {"root": root_id},
    )
    deleted = await session.execute(
        text(_SUBTREE_CTE + " DELETE FROM files WHERE id IN (SELECT id FROM sub)"),
        {"root": root_id},
    )
    rows = deleted.rowcount or 0
    for owner_id, size in size_by_owner.items():
        if size:
            await _release_quota(session, owner_id, size)
    await session.commit()

    # 3) 삭제 사실을 알린다 — 열려 있는 휴지통 화면이 갱신되도록. 소프트 삭제/복원과 달리
    #    행이 사라져 구독자 필터가 조회로 판정할 수 없으므로 소유자 전용 이벤트를 쓴다.
    if publish:
        await file_events_service.publish_purge_event(
            file_id=root_id,
            parent_folder_id=parent_id,
            actor_id=actor_id,
            name=name,
            owner_id=root_owner_id,
        )

    # 4) 오브젝트 정리 — best-effort (실패해도 DB 는 이미 일관).
    if keys:
        await storage.delete_many_async(list(keys))

    return rows, sum(size_by_owner.values())
