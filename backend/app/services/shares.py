"""공유 링크 서비스 (PRD 3.4, 5.4, 6.3, 10장).

게이트웨이 모델(PRD 2.2)이므로 공개 접근은 매 요청 DB 로 검증한다 — 비활성화/만료/횟수
초과가 즉시 반영된다. presigned URL 은 브라우저에 노출하지 않고, X-Accel-Redirect 로 nginx→
MinIO 스트리밍을 유도한다(라우트에서 gateway_download_response 로 응답).

폴더 공유는 방문자가 웹에서 트리를 탐색하고(목록/개별 미리보기/개별 다운로드), 전체 또는
하위 폴더를 스트리밍 ZIP 으로 받을 수 있다(archives 재사용). 접근 판정은 방문자가 아니라
**공유 생성자** 권한으로 한다 — 생성자가 볼 수 없는 하위 항목은 목록에도 ZIP 에도 나가지
않는다. 하위 항목 접근은 매번 "공유된 루트의 자손인가"를 재귀 CTE 로 검증한다 — 이 검증이
없으면 공유 링크 하나로 임의 파일을 열람하는 구멍이 된다. 폴더 공유는 다운로드 횟수 제한
(max_downloads)을 두지 않는다 — 접근 제한 수단은 만료 기간(과 비밀번호)뿐이다.

상태별 응답(PRD 10장):
  - 공유 없음            → 404
  - is_active = FALSE    → 410 (비활성화 즉시 차단)
  - 만료(expires_at)     → 410
  - 파일 소프트 삭제      → 410
  - 비밀번호 불일치       → 401 (열거 방지 위해 메시지 일반화)
  - max_downloads 초과   → 410
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import File, Share, User
from app.models.enums import SharePermission, UserStatus
from app.services import archives as archives_service
from app.services import permissions as permissions_service
from app.services import previews as previews_service
from app.services.archives import ArchivePlan
from app.services.files import FileServiceError
from app.services.previews import PreviewPlan
from app.services.storage import StorageService

# Phase 1 허용 권한. WRITE(편집)는 PRD Phase 5.
_ALLOWED_PERMISSIONS = {SharePermission.READ, SharePermission.DOWNLOAD}

# share_url 토큰 — secrets.token_urlsafe(16) ≈ 22 자, shares.share_url VARCHAR(64) 이내.
_TOKEN_BYTES = 16
# 유니크 충돌 시 재생성 시도 횟수(사실상 도달 불가, 방어적).
_MAX_TOKEN_ATTEMPTS = 5


class ShareServiceError(Exception):
    """공유 링크 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


def _is_expired(share: Share) -> bool:
    """만료 시각이 지났는지. expires_at 이 NULL 이면 만료 없음."""
    if share.expires_at is None:
        return False
    expires_at = share.expires_at
    if expires_at.tzinfo is None:  # 방어적 — TIMESTAMPTZ 라 보통 aware.
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= _now()


# --- 인증 사용자용 -----------------------------------------------------------


async def create_share(
    session: AsyncSession,
    user: User,
    *,
    file_id: int,
    permission: str,
    expires_at: datetime | None,
    password: str | None,
    max_downloads: int | None,
) -> tuple[Share, str, bool]:
    """공유 링크 생성 (PRD 6.3). 소유자 또는 write 이상 권한자. (share, 이름, 폴더 여부) 반환.

    폴더도 공유할 수 있다 — 방문자는 하위 전체를 ZIP 으로 내려받는다(plan_share_archive).
    share_url 은 secrets.token_urlsafe 로 생성하고 유니크 충돌 시 재생성한다.
    """
    try:
        perm = SharePermission(permission)
    except ValueError as exc:
        raise ShareServiceError(422, "지원하지 않는 공유 권한입니다.") from exc
    if perm not in _ALLOWED_PERMISSIONS:
        raise ShareServiceError(400, "편집 권한 공유는 아직 지원하지 않습니다.")

    # 접근 검사 + 폴더/삭제 파일 배제. 소유자가 아니어도 그룹 권한(상속 포함)이 write 이상이면
    # 공유할 수 있다 — 내 폴더에 협업자가 올린 파일도 공유 링크를 걸 수 있어야 하기 때문이다.
    # read 조차 없으면 404(존재 여부 노출 방지), 볼 수는 있으나 write 미만이면 403 으로 구분한다.
    file = await session.get(File, file_id)
    if file is None or file.is_deleted:
        raise ShareServiceError(404, "파일을 찾을 수 없습니다.")
    if file.user_id != user.id:
        level = await permissions_service.get_access_level(session, user, file)
        if level is None:
            raise ShareServiceError(404, "파일을 찾을 수 없습니다.")
        if not permissions_service.permission_covers(level, "write"):
            raise ShareServiceError(403, "이 파일을 공유할 권한이 없습니다.")
    file_name = file.name
    is_folder = file.is_folder

    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= _now():
            raise ShareServiceError(422, "만료 시각은 현재보다 이후여야 합니다.")

    if max_downloads is not None and max_downloads < 1:
        raise ShareServiceError(422, "max_downloads 는 1 이상이어야 합니다.")
    # 폴더 공유는 횟수 제한 없이 열어 둔다 — 접근 제한은 만료 기간(과 비밀번호)으로만.
    # 개별 파일을 몇 번이고 받는 탐색형 공유에서 횟수는 의미 있는 단위가 아니기 때문이다.
    if is_folder and max_downloads is not None:
        raise ShareServiceError(
            422, "폴더 공유는 다운로드 횟수 제한을 지원하지 않습니다."
        )

    password_hash = hash_password(password) if password else None

    for _ in range(_MAX_TOKEN_ATTEMPTS):
        share = Share(
            file_id=file.id,
            created_by=user.id,
            share_url=secrets.token_urlsafe(_TOKEN_BYTES),
            permission=perm.value,
            password_hash=password_hash,
            expires_at=expires_at,
            max_downloads=max_downloads,
        )
        session.add(share)
        try:
            await session.commit()
        except IntegrityError:
            # share_url 유니크 충돌 — 새 토큰으로 재시도.
            await session.rollback()
            continue
        await session.refresh(share)
        return share, file_name, is_folder

    raise ShareServiceError(500, "공유 링크 생성에 실패했습니다. 다시 시도해 주세요.")


async def list_shares(
    session: AsyncSession,
    user: User,
    *,
    active: bool | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[tuple[Share, str, bool]], int]:
    """내 공유 링크 목록 (파일명 포함, 최신순) — PRD 6.3. ((share, 이름, 폴더 여부) 목록, 총 개수).

    active 는 활성/비활성 탭 필터(None 이면 전체). 총 개수는 필터를 적용한 뒤 센다 —
    페이지 수 계산이 현재 탭 기준이어야 하기 때문이다.
    """
    base = (
        select(Share, File.name, File.is_folder)
        .join(File, Share.file_id == File.id)
        .where(Share.created_by == user.id)
    )
    count_q = select(func.count()).select_from(Share).where(Share.created_by == user.id)
    if active is not None:
        base = base.where(Share.is_active.is_(active))
        count_q = count_q.where(Share.is_active.is_(active))

    total = (await session.execute(count_q)).scalar_one()
    rows = (
        await session.execute(
            base.order_by(Share.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return [(share, name, is_folder) for share, name, is_folder in rows], total


async def get_owned_share(session: AsyncSession, user: User, share_id: int) -> Share:
    """소유자 본인의 공유 링크를 반환한다(통계 조회 등). 없거나 미소유면 404."""
    share = await session.get(Share, share_id)
    if share is None or share.created_by != user.id:
        raise ShareServiceError(404, "공유 링크를 찾을 수 없습니다.")
    return share


async def disable_share(session: AsyncSession, user: User, share_id: int) -> None:
    """공유 링크 비활성화 (PRD 3.4, 6.3). is_active=FALSE — 행 삭제가 아니라 이력 보존.

    게이트웨이 모델이라 다음 공개 요청부터 즉시 410 으로 차단된다. 멱등(이미 비활성이면 무시).
    """
    share = await session.get(Share, share_id)
    if share is None or share.created_by != user.id:
        raise ShareServiceError(404, "공유 링크를 찾을 수 없습니다.")
    if share.is_active:
        share.is_active = False
        await session.commit()


# --- 공개(무인증) 접근 -------------------------------------------------------


async def _get_by_url(session: AsyncSession, share_url: str) -> Share:
    share = (
        await session.execute(select(Share).where(Share.share_url == share_url))
    ).scalar_one_or_none()
    if share is None:
        raise ShareServiceError(404, "공유 링크를 찾을 수 없습니다.")
    return share


async def get_share_by_url(session: AsyncSession, share_url: str) -> Share | None:
    """share_url 로 Share 를 찾는다(상태 검사 없음). 접근 통계 기록 등 부수 용도. 없으면 None."""
    return (
        await session.execute(select(Share).where(Share.share_url == share_url))
    ).scalar_one_or_none()


def _ensure_accessible(share: Share) -> None:
    """활성·미만료 검증. 위반 시 410 (게이트웨이 즉시 차단)."""
    if not share.is_active:
        raise ShareServiceError(410, "비활성화된 공유 링크입니다.")
    if _is_expired(share):
        raise ShareServiceError(410, "만료된 공유 링크입니다.")


async def get_share_meta(session: AsyncSession, share_url: str) -> tuple[Share, File]:
    """공개 공유 메타 조회 (PRD 6.3). 상태별 404/410 을 서비스가 낸다."""
    share = await _get_by_url(session, share_url)
    _ensure_accessible(share)
    file = await session.get(File, share.file_id)
    if file is None or file.is_deleted:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    return share, file


async def _try_increment(session: AsyncSession, share_id: int) -> bool:
    """download_count 를 원자적으로 증가한다. max_downloads 초과 시 0 rows → False.

    조건부 UPDATE(WHERE download_count < max_downloads)로 동시 다운로드 레이스를 차단한다
    (PRD 5.10 원자적 갱신과 동일 패턴). is_active 도 함께 재확인해 비활성화 직후 우회를 막는다.
    """
    result = await session.execute(
        text(
            "UPDATE shares "
            "SET download_count = download_count + 1, updated_at = now() "
            "WHERE id = :id AND is_active = TRUE "
            "AND (max_downloads IS NULL OR download_count < max_downloads) "
            "RETURNING download_count"
        ),
        {"id": share_id},
    )
    row = result.first()
    await session.commit()
    return row is not None


async def authorize_share_access(
    session: AsyncSession,
    share_url: str,
    password: str | None,
) -> tuple[Share, File]:
    """공개 접근 공통 관문 — 횟수는 소모하지 않는다. (share, file) 반환.

    검증 순서: 활성 → 만료 → 파일 존재 → 비밀번호(불일치 401 — 열거/타이밍 노출을 줄이기
    위해 오류 메시지는 일반화한다). 다운로드 계열은 이후 consume_download_quota 로 횟수를
    소모한다 — 폴더 공유는 그 사이에 아카이브 계획(413 상한 검사)이 끼어들 수 있어야 하므로
    인가와 소모를 분리했다.
    """
    share = await _get_by_url(session, share_url)
    _ensure_accessible(share)

    file = await session.get(File, share.file_id)
    if file is None or file.is_deleted:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")

    if share.password_hash is not None:
        if not password or not verify_password(password, share.password_hash):
            raise ShareServiceError(401, "비밀번호가 필요하거나 올바르지 않습니다.")

    return share, file


async def consume_download_quota(session: AsyncSession, share: Share) -> None:
    """다운로드 횟수 원자적 소모 — 초과면 410. 비밀번호(인가) 통과 후에만 호출한다."""
    if not await _try_increment(session, share.id):
        raise ShareServiceError(410, "다운로드 횟수를 모두 사용한 공유 링크입니다.")


async def presign_share_file(
    storage: StorageService, file: File
) -> tuple[str, str, str]:
    """단일 파일 공유의 스트리밍 준비 — 내부 presign(60s)을 nginx `/_minio/` 경로로 변환.

    반환: (internal_redirect, filename, mime). 인가·횟수 소모는 호출자가 끝냈어야 한다.
    """
    presigned = await storage.presign_get_async(file.file_key)
    internal = storage.to_internal_redirect(presigned)
    return internal, file.name, file.mime_type or "application/octet-stream"


# 하위 노드에서 부모를 따라 뿌리까지 올라가는 체인. 공유 루트가 체인에 있으면 "공유 트리 안"
# 이고, 그 체인이 곧 브레드크럼이 된다. 삭제된 조상이 끼어 있으면 체인이 끊겨 자연히 배제된다.
_UP_CHAIN_SQL = text(
    """
    WITH RECURSIVE up AS (
        SELECT id, name, parent_folder_id, 0 AS depth
        FROM files WHERE id = :node AND is_deleted = FALSE
        UNION ALL
        SELECT f.id, f.name, f.parent_folder_id, up.depth + 1
        FROM files f JOIN up ON f.id = up.parent_folder_id
        WHERE f.is_deleted = FALSE
    )
    SELECT id, name, depth FROM up ORDER BY depth
    """
)


async def _get_active_creator(session: AsyncSession, share: Share) -> User:
    """공유 생성자 — 폴더 공유의 접근 판정 주체. 없거나 비활성이면 410 (링크 즉시 차단)."""
    creator = await session.get(User, share.created_by)
    if creator is None or creator.status != UserStatus.ACTIVE:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    return creator


async def _resolve_share_node(
    session: AsyncSession, root: File, node_id: int
) -> tuple[File, list[tuple[int, str]]]:
    """node_id 가 공유 루트의 자손(또는 루트 자신)인지 검증한다. 트리 밖이면 404 —
    존재 여부를 노출하지 않는다. (node, 루트→node 브레드크럼[(id, name)]) 반환.
    """
    node = await session.get(File, node_id)
    if node is None or node.is_deleted:
        raise ShareServiceError(404, "항목을 찾을 수 없습니다.")
    rows = (await session.execute(_UP_CHAIN_SQL, {"node": node_id})).all()
    chain = [(row.id, row.name) for row in rows]  # node → … → 최상위 순
    for idx, (chain_id, _name) in enumerate(chain):
        if chain_id == root.id:
            return node, list(reversed(chain[: idx + 1]))
    raise ShareServiceError(404, "항목을 찾을 수 없습니다.")


async def authorize_share_child(
    session: AsyncSession,
    share_url: str,
    password: str | None,
    node_id: int,
) -> tuple[Share, File, list[tuple[int, str]]]:
    """폴더 공유 안의 하위 항목 접근 인가. (share, node, 브레드크럼) 반환.

    공유 인가(활성/만료/비밀번호) → 루트가 폴더인지(400) → node 가 트리 안인지(404) →
    생성자 권한이 하위까지 미치는지(inherit 없는 부여면 404 — 목록에도 없던 항목이다).
    """
    share, root = await authorize_share_access(session, share_url, password)
    if not root.is_folder:
        raise ShareServiceError(400, "폴더 공유가 아닙니다.")
    creator = await _get_active_creator(session, share)
    node, crumbs = await _resolve_share_node(session, root, node_id)
    if node.id != root.id and not await permissions_service.can_access_descendants(
        session, creator, root
    ):
        raise ShareServiceError(404, "항목을 찾을 수 없습니다.")
    return share, node, crumbs


async def list_share_folder(
    session: AsyncSession,
    share_url: str,
    password: str | None,
    folder_id: int | None,
) -> tuple[Share, File, list[tuple[int, str]], list[File]]:
    """폴더 공유의 웹 탐색 목록. (share, 현재 폴더, 루트→현재 브레드크럼, 자식들) 반환.

    folder_id 가 None 이면 공유 루트. 생성자 권한이 하위까지 미치지 않으면 루트를 빈
    폴더로 보여준다 — ZIP 이 빈 폴더만 담는 것과 같은 기준이다.
    """
    share, root = await authorize_share_access(session, share_url, password)
    if not root.is_folder:
        raise ShareServiceError(400, "폴더 공유가 아닙니다.")
    creator = await _get_active_creator(session, share)

    descend = await permissions_service.can_access_descendants(session, creator, root)
    if folder_id is None or folder_id == root.id:
        target, crumbs = root, [(root.id, root.name)]
    else:
        if not descend:
            raise ShareServiceError(404, "항목을 찾을 수 없습니다.")
        target, crumbs = await _resolve_share_node(session, root, folder_id)
        if not target.is_folder:
            raise ShareServiceError(404, "항목을 찾을 수 없습니다.")

    if not descend:
        return share, target, crumbs, []

    children = (
        (
            await session.execute(
                select(File)
                .where(
                    File.parent_folder_id == target.id,
                    File.is_deleted.is_(False),
                )
                .order_by(File.is_folder.desc(), File.name.asc())
            )
        )
        .scalars()
        .all()
    )
    return share, target, crumbs, list(children)


async def prepare_share_child_preview(
    session: AsyncSession,
    storage: StorageService,
    share_url: str,
    password: str | None,
    file_id: int,
) -> tuple[PreviewPlan, str, int]:
    """폴더 공유 안의 파일 하나 미리보기 (횟수와 무관). (plan, filename, share_id) 반환."""
    share, node, _crumbs = await authorize_share_child(
        session, share_url, password, file_id
    )
    if node.is_folder:
        raise ShareServiceError(400, "폴더는 미리볼 수 없습니다.")
    plan = await previews_service.build_preview_plan(storage, node)
    return plan, node.name, share.id


async def plan_share_archive(
    session: AsyncSession, share: Share, folder: File
) -> ArchivePlan:
    """공유된 폴더를 ZIP 아카이브 계획으로 펼친다 (개수/용량 상한 검사 포함).

    방문자는 익명이므로 접근 판정은 **공유 생성자** 권한으로 한다 — 생성자가 지금 볼 수
    있는 범위만 나간다(생성 뒤 권한이 회수됐거나 계정이 비활성이면 410). 상한 초과(413)는
    그대로 올리고, 그 외 실패는 내부 사정을 숨기고 410 으로 일반화한다.

    folder 는 공유 루트 자체이거나 그 자손 폴더다(하위 폴더 ZIP) — 자손 검증은 호출자
    (authorize_share_child / prepare_ticketed_share_archive)가 끝냈어야 한다.
    """
    creator = await _get_active_creator(session, share)
    try:
        return await archives_service.plan_archive(session, creator, [folder.id])
    except FileServiceError as exc:
        if exc.status_code == 413:
            raise ShareServiceError(413, exc.detail) from exc
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.") from exc


async def prepare_ticketed_share_archive(
    session: AsyncSession, share_id: int, file_id: int
) -> ArchivePlan:
    """공개 폴더 공유 티켓 소비 후 ZIP 계획 재수립. 인가·횟수 소모는 발급 시 끝났으므로,
    스트리밍 직전 판정(트리 소속·생성자 권한·상한)만 다시 한다.

    file_id 는 공유 루트 또는 그 자손 폴더(하위 폴더 ZIP) — 발급 시 검증했지만 그 사이
    이동/삭제됐을 수 있어 다시 확인한다.
    """
    share = await session.get(Share, share_id)
    if share is None:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    root = await session.get(File, share.file_id)
    if root is None or root.is_deleted or not root.is_folder:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    folder, _crumbs = await _resolve_share_node(session, root, file_id)
    if not folder.is_folder:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    return await plan_share_archive(session, share, folder)


async def authorize_share_view(
    session: AsyncSession,
    share_url: str,
    password: str | None,
) -> tuple[Share, File]:
    """공개 미리보기 인가 (PRD 3.2, 3.4). 활성/만료/파일/비밀번호를 검증하되 download_count 는
    소모하지 않는다 — 미리보기는 다운로드 횟수 제한과 무관하다. 폴더는 400. (share, file) 반환.
    """
    share, file = await authorize_share_access(session, share_url, password)
    if file.is_folder:
        raise ShareServiceError(400, "폴더는 미리볼 수 없습니다.")
    return share, file


async def prepare_share_preview(
    session: AsyncSession,
    storage: StorageService,
    share_url: str,
    password: str | None,
) -> tuple[PreviewPlan, str, int, int]:
    """공개 미리보기 준비 (PRD 3.2). 인가(횟수 미소모) 후 (plan, filename, share_id, file_id) 반환.

    지원 타입은 게이트웨이 인라인(image/pdf), 텍스트 head, 영상 스트림(mp4)이고 미지원은
    plan.kind=="unsupported". file_id 는 영상 스트림 티켓이 대상을 다시 찾는 데 쓴다.
    """
    share, file = await authorize_share_view(session, share_url, password)
    plan = await previews_service.build_preview_plan(storage, file)
    return plan, file.name, share.id, file.id


async def prepare_ticketed_share_preview(
    session: AsyncSession,
    storage: StorageService,
    share_id: int,
    file_id: int,
) -> tuple[str, str, str]:
    """공개 영상 미리보기 스트림 준비. 반환: (internal_redirect, filename, mime).

    미리보기 티켓은 재생 내내 재사용되므로 발급 시 판정을 믿지 않고 매 Range 요청마다 다시 본다:
    공유가 살아 있는지(비활성·만료 410), 대상이 아직 공유 트리 안의 영상 파일인지(404).
    비밀번호는 티켓을 받을 때 이미 통과했으므로 다시 묻지 않는다 — 티켓 자체가 그 증표다.
    다운로드 횟수는 건드리지 않는다(미리보기는 다운로드가 아니다).
    """
    share = await session.get(Share, share_id)
    if share is None:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    _ensure_accessible(share)

    root = await session.get(File, share.file_id)
    if root is None or root.is_deleted:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    node, _crumbs = await _resolve_share_node(session, root, file_id)
    if node.is_folder or previews_service.classify(node.mime_type) != "video":
        raise ShareServiceError(404, "항목을 찾을 수 없습니다.")
    return await presign_share_file(storage, node)


async def prepare_ticketed_share_download(
    session: AsyncSession,
    storage: StorageService,
    file_id: int,
) -> tuple[str, str, str]:
    """공개 공유 티켓 소비 후 다운로드 준비. 인가·횟수 소모는 티켓 발급 시 이미 끝났으므로,
    여기서는 파일 유효성만 확인하고 presign 한다. 반환: (internal_redirect, filename, mime).
    """
    file = await session.get(File, file_id)
    if file is None or file.is_deleted:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    return await presign_share_file(storage, file)
