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
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.metrics import observe_download_bytes
from app.models import File, FileGroupPermission, FileVersion, Group, User
from app.services import file_events as file_events_service
from app.services import permissions as permissions_service
from app.services import previews as previews_service
from app.services import thumbnails as thumbnails_service
from app.services.groups import get_user_group_ids
from app.services.previews import PreviewPlan
from app.services.storage import StorageService

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


async def annotate_location(
    session: AsyncSession, user: User, files: list[File]
) -> None:
    """각 파일에 조상 폴더 경로 문자열 `location` 을 in-place 부착한다(최근·즐겨찾기 위치 표기용).

    경로는 최상위부터 직속 부모까지 폴더명을 " / " 로 이은 것이다. 루트 폴더(name='root')는
    실제 이름 대신 소유 여부에 따라 "내 드라이브"(내 파일) 또는 "내 드라이브 / 공유"(타인 공유)로
    대체한다 — 통합 드라이브에서 "공유됨"이 내 드라이브 아래 가상 "공유" 폴더로 합쳐지므로 위치
    문자열도 이에 맞춘다. 부모 체인은 필요한 폴더만 배치로 모아 조회하므로(레벨당 IN 1회) N+1 회피.
    """
    cache: dict[int, tuple[str, int | None]] = {}  # folder_id -> (name, parent_id)
    need = {f.parent_folder_id for f in files if f.parent_folder_id is not None}
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

    for f in files:
        names: list[str] = []
        pid = f.parent_folder_id
        while pid is not None and pid in cache:
            name, parent = cache[pid]
            if parent is None:  # 루트 폴더 도달 — 실제 이름은 넣지 않고 접두사로 대체
                break
            names.append(name)
            pid = parent
        names.reverse()
        prefix = "내 드라이브" if f.user_id == user.id else "내 드라이브 / 공유"
        f.location = " / ".join([prefix, *names])  # type: ignore[attr-defined]


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
    user_group_ids = await get_user_group_ids(session, user.id)
    user_group_id_set = set(user_group_ids)

    for f in files:
        directs = directs_by_file.get(f.id, [])
        if f.user_id == user.id:
            # 소유 항목 — 전권. group_names 는 내가 공유한 그룹들(직접 부여 그룹명).
            # (file,group) 는 UNIQUE 이므로 한 파일의 그룹명은 서로 다르다 — 중복 제거 불필요.
            f.permission = "owner"  # type: ignore[attr-defined]
            f.group_names = [gname for _, _, gname in directs]  # type: ignore[attr-defined]
            continue
        # 공유받은 항목 — 파일 자체의 직접 부여(내 그룹) 우선.
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


def _child_listing_query(parent_id: int) -> Select:
    """활성 하위 항목 — 폴더 우선, 이름순 (PRD 6.2)."""
    return (
        select(File)
        .where(File.parent_folder_id == parent_id, File.is_deleted.is_(False))
        .order_by(File.is_folder.desc(), File.name.asc())
    )


async def list_children(
    session: AsyncSession, user: User, parent_id: int | None, page: int, size: int
) -> tuple[list[File], int]:
    """폴더 내 항목 목록 + 총 개수 (페이지네이션)."""
    parent = await _resolve_parent(session, user, parent_id)

    total = (
        await session.execute(
            select(func.count())
            .select_from(File)
            .where(File.parent_folder_id == parent.id, File.is_deleted.is_(False))
        )
    ).scalar_one()

    offset = (page - 1) * size
    rows = (
        await session.execute(
            _child_listing_query(parent.id).offset(offset).limit(size)
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
    # 재업로드/버전 복구/재개 업로드(version)가 모두 이 경로를 지난다 — 단일 발행 지점.
    await file_events_service.publish_file_event(
        type="version",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=uploaded_by,
        name=file.name,
    )
    return file


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
    return file


async def permanent_delete(
    session: AsyncSession, storage: StorageService, user: User, file_id: int
) -> None:
    """영구 삭제 (PRD 6.2). 휴지통 항목만 허용. 폴더는 하위 전체 재귀.

    MinIO 오브젝트(원본+버전) 삭제 + storage_used 감소 + files 행 삭제(versions CASCADE).
    DB 를 먼저 확정하고 오브젝트는 best-effort 로 정리해 DB 를 진실 소스로 유지한다.
    """
    file = await ensure_file_access(session, user, await get_file(session, file_id), need="manage")
    if not file.is_deleted:
        raise FileServiceError(409, "휴지통에 있는 항목만 영구 삭제할 수 있습니다.")

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
            {"root": file.id},
        )
    ).all()
    keys: set[str] = {r.file_key for r in file_rows if r.file_key}
    # 썸네일 오브젝트도 함께 제거한다 (thumbnails/{fileId}.png). 할당량에는 포함하지 않는다.
    keys.update(r.thumbnail_key for r in file_rows if r.thumbnail_key)

    version_rows = (
        await session.execute(
            text(
                _SUBTREE_CTE
                + " SELECT object_key, size FROM file_versions "
                "WHERE file_id IN (SELECT id FROM sub)"
            ),
            {"root": file.id},
        )
    ).all()
    total_size = sum(r.size for r in version_rows)
    keys.update(r.object_key for r in version_rows if r.object_key)

    # 2) DB 확정 — 행 삭제(versions CASCADE) + storage_used 감소.
    #    shares.file_id 는 ON DELETE CASCADE 가 없어(모델/마이그레이션 변경 회피), 영구 삭제
    #    대상(하위 포함)에 걸린 공유 링크 행을 앱 레벨에서 먼저 지워 FK 위반을 방지한다.
    #    공유 링크는 이력 보존 대상이지만 원본 파일 자체가 사라지는 영구 삭제에서는 함께 소멸한다.
    await session.execute(
        text(_SUBTREE_CTE + " DELETE FROM shares WHERE file_id IN (SELECT id FROM sub)"),
        {"root": file.id},
    )
    await session.execute(
        text(_SUBTREE_CTE + " DELETE FROM files WHERE id IN (SELECT id FROM sub)"),
        {"root": file.id},
    )
    if total_size:
        await _release_quota(session, user.id, total_size)
    await session.commit()

    # 3) 오브젝트 정리 — best-effort (실패해도 DB 는 이미 일관).
    if keys:
        await storage.delete_many_async(list(keys))
