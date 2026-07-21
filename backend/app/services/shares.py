"""공유 링크 서비스 (PRD 3.4, 5.4, 6.3, 10장).

게이트웨이 모델(PRD 2.2)이므로 공개 접근은 매 요청 DB 로 검증한다 — 비활성화/만료/횟수
초과가 즉시 반영된다. presigned URL 은 브라우저에 노출하지 않고, X-Accel-Redirect 로 nginx→
MinIO 스트리밍을 유도한다(라우트에서 gateway_download_response 로 응답).

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

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import File, Share, User
from app.models.enums import SharePermission
from app.services import permissions as permissions_service
from app.services import previews as previews_service
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
) -> tuple[Share, str]:
    """공유 링크 생성 (PRD 6.3). 소유자 또는 write 이상 권한자, 폴더 불가. (share, 파일명) 반환.

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
    if file.is_folder:
        raise ShareServiceError(400, "폴더는 공유할 수 없습니다.")
    file_name = file.name

    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= _now():
            raise ShareServiceError(422, "만료 시각은 현재보다 이후여야 합니다.")

    if max_downloads is not None and max_downloads < 1:
        raise ShareServiceError(422, "max_downloads 는 1 이상이어야 합니다.")

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
        return share, file_name

    raise ShareServiceError(500, "공유 링크 생성에 실패했습니다. 다시 시도해 주세요.")


async def list_shares(session: AsyncSession, user: User) -> list[tuple[Share, str]]:
    """내 공유 링크 목록 (파일명 포함, 최신순) — PRD 6.3."""
    rows = (
        await session.execute(
            select(Share, File.name)
            .join(File, Share.file_id == File.id)
            .where(Share.created_by == user.id)
            .order_by(Share.created_at.desc())
        )
    ).all()
    return [(share, name) for share, name in rows]


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


async def authorize_share_download(
    session: AsyncSession,
    share_url: str,
    password: str | None,
) -> tuple[File, str]:
    """공개 다운로드 인가 + 횟수 소모 (PRD 6.3, 10장). presign 은 하지 않는다.

    검증 순서: 활성 → 만료 → 파일 존재 → 비밀번호(불일치 401) → max_downloads(초과 410).
    통과 시 download_count 를 원자적으로 증가하고 (file, permission) 을 반환한다.
    직접 다운로드와 티켓 발급이 공유하는 공통 관문이다.
    """
    share = await _get_by_url(session, share_url)
    _ensure_accessible(share)

    file = await session.get(File, share.file_id)
    if file is None or file.is_deleted:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")

    # 비밀번호 검증 — 열거/타이밍 노출을 줄이기 위해 오류 메시지는 일반화한다.
    if share.password_hash is not None:
        if not password or not verify_password(password, share.password_hash):
            raise ShareServiceError(401, "비밀번호가 필요하거나 올바르지 않습니다.")

    # 다운로드 횟수 원자적 증가 — 초과면 410. (비밀번호 통과 후에만 카운트를 소모한다.)
    if not await _try_increment(session, share.id):
        raise ShareServiceError(410, "다운로드 횟수를 모두 사용한 공유 링크입니다.")

    return file, share.permission


async def prepare_share_download(
    session: AsyncSession,
    storage: StorageService,
    share_url: str,
    password: str | None,
) -> tuple[str, str, str, str]:
    """공개 다운로드 준비 (PRD 6.3, 10장).

    인가+횟수 소모 후 내부 presign(60s)을 nginx `/_minio/` 경로로 변환해 반환한다.
    반환: (internal_redirect, filename, mime, permission).
    """
    file, permission = await authorize_share_download(session, share_url, password)
    presigned = await storage.presign_get_async(file.file_key)
    internal = storage.to_internal_redirect(presigned)
    mime = file.mime_type or "application/octet-stream"
    # permission=read 여도 Phase 1 은 다운로드와 동일 취급하되, 구분은 응답에 담아 전달한다.
    return internal, file.name, mime, permission


async def authorize_share_view(
    session: AsyncSession,
    share_url: str,
    password: str | None,
) -> tuple[Share, File]:
    """공개 미리보기 인가 (PRD 3.2, 3.4). 활성/만료/파일/비밀번호를 검증하되 download_count 는
    소모하지 않는다 — 미리보기는 다운로드 횟수 제한과 무관하다. 폴더는 400. (share, file) 반환.
    """
    share = await _get_by_url(session, share_url)
    _ensure_accessible(share)
    file = await session.get(File, share.file_id)
    if file is None or file.is_deleted:
        raise ShareServiceError(410, "공유가 더 이상 유효하지 않습니다.")
    if file.is_folder:
        raise ShareServiceError(400, "폴더는 미리볼 수 없습니다.")
    if share.password_hash is not None:
        if not password or not verify_password(password, share.password_hash):
            raise ShareServiceError(401, "비밀번호가 필요하거나 올바르지 않습니다.")
    return share, file


async def prepare_share_preview(
    session: AsyncSession,
    storage: StorageService,
    share_url: str,
    password: str | None,
) -> tuple[PreviewPlan, str, int]:
    """공개 미리보기 준비 (PRD 3.2). 인가(횟수 미소모) 후 (plan, filename, share_id) 반환.

    지원 타입은 게이트웨이 인라인(image/pdf) 또는 텍스트 head, 미지원은 plan.kind=="unsupported".
    """
    share, file = await authorize_share_view(session, share_url, password)
    plan = await previews_service.build_preview_plan(storage, file)
    return plan, file.name, share.id


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
    presigned = await storage.presign_get_async(file.file_key)
    internal = storage.to_internal_redirect(presigned)
    mime = file.mime_type or "application/octet-stream"
    return internal, file.name, mime
