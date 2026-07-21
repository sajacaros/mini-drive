"""재개 가능 업로드 서비스 (PRD 3.2 — S3 Multipart Upload 기반).

흐름: 세션 개시(init) → 파트 업로드(backend 경유 스트리밍) → 완료(파트 병합) →
기존 파일 생성/버전 경로 합류. 중단 시 MinIO 에 스테이징된 파트를 조회(list_parts)해
이어올린다. 게이트웨이 모델을 지켜 presigned 파트 URL 을 클라이언트에 노출하지 않는다.

핵심 설계 결정:
  - 세션 상태는 DB(`upload_sessions`) — 대용량 업로드는 장시간 이어지고 고아 정리를 위해
    만료 세션을 열거해야 하므로 짧은 TTL 의 Redis 티켓과 맞지 않는다. 파트 etag 는 저장하지
    않고 MinIO(`list_parts`)를 진실 소스로 삼아 재개/완료 시 재구성한다.
  - 새 파일(kind='new')은 완료 시점의 files.id 를 nextval 로 미리 선점해 그 원본 키
    (users/{uid}/{fileId})에 곧바로 멀티파트를 개시한다. 임시 키에 병합 후 복사하는 방식은
    최대 10GB 파일에서 S3 CopyObject 5GiB 제약에 걸리므로 회피한다.
  - 재업로드(kind='version')는 현재 원본 키(file_key)에 멀티파트를 개시한다. 확정 오브젝트는
    CompleteMultipartUpload 전까지 바뀌지 않으므로 현재 버전이 안전하고, 완료 시 기존 재업로드와
    동일한 `_commit_new_version`(스냅샷→버저닝→썸네일→할당량) 경로에 합류한다.
  - 할당량은 완료 시점에 실제 저장 크기 기준으로 원자적 확정(_reserve_quota)한다 — 완료
    트랜잭션과 동일 tx 라 롤백이 선점을 함께 취소한다(기존 업로드와 동일). 개시 시에는 소프트
    사전검사만 해 빠른 실패를 준다. 과금 회피(무한 스테이징)를 막기 위해 할당량 초과 완료는
    세션을 폐기(멀티파트 abort)한다.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import File, FileVersion, UploadSession, User
from app.services import file_events as file_events_service
from app.services import files as files_service
from app.services import thumbnails as thumbnails_service
from app.services.files import FileServiceError, build_file_key
from app.services.storage import StorageService

# 세션 토큰 — 불투명·추측 불가. String(64) 에 여유 있게 들어간다.
_SESSION_TOKEN_BYTES = 24

# 세션 개시 시 기회적으로 정리할 만료 세션 최대 개수(요청 지연 최소화).
_CLEANUP_BATCH = 10


def _now() -> datetime:
    return datetime.now(UTC)


def _new_token() -> str:
    return secrets.token_urlsafe(_SESSION_TOKEN_BYTES)


def _total_parts(total_size: int, part_size: int) -> int:
    return (total_size + part_size - 1) // part_size


def _expected_part_size(
    part_number: int, total_parts: int, total_size: int, part_size: int
) -> int:
    """해당 파트가 가져야 할 정확한 바이트 수(마지막 파트만 나머지)."""
    if part_number < total_parts:
        return part_size
    return total_size - part_size * (total_parts - 1)


def uploaded_part_numbers(parts: list[tuple[int, int, str]]) -> list[int]:
    return sorted(pn for pn, _size, _etag in parts)


def received_bytes(parts: list[tuple[int, int, str]]) -> int:
    return sum(size for _pn, size, _etag in parts)


# --- 세션 로드 / 정리 --------------------------------------------------------


async def _load_session(
    session: AsyncSession, user: User, session_id: str
) -> UploadSession:
    """세션을 소유자 검사와 함께 로드한다. 없거나 타인 세션이면 404(존재 노출 방지)."""
    row = await session.get(UploadSession, session_id)
    if row is None or row.user_id != user.id:
        raise FileServiceError(404, "업로드 세션을 찾을 수 없습니다.")
    return row


async def _reserve_file_id(session: AsyncSession) -> int:
    """완료 시 생성할 files.id 를 시퀀스에서 미리 선점한다(행은 아직 만들지 않음)."""
    result = await session.execute(
        text("SELECT nextval(pg_get_serial_sequence('files','id'))")
    )
    return int(result.scalar_one())


async def _discard(
    session: AsyncSession,
    storage: StorageService,
    *,
    session_id: str,
    object_key: str,
    upload_id: str,
) -> None:
    """세션을 폐기한다: 멀티파트 스테이징 abort + 세션 행 삭제(best-effort).

    확정 오브젝트(object_key)는 건드리지 않는다 — 'version' 은 현재 라이브 파일이고
    'new' 는 아직 오브젝트가 아니다. 호출 전 트랜잭션 롤백이 선행되므로 인자는 원시값으로
    받는다(롤백으로 만료된 ORM 인스턴스 접근은 async 에서 암묵 IO 를 유발).
    """
    try:
        await storage.abort_multipart_async(object_key, upload_id)
    except Exception:  # noqa: BLE001 - best-effort
        pass
    try:
        await session.execute(
            delete(UploadSession).where(UploadSession.id == session_id)
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()


async def cleanup_expired(
    session: AsyncSession, storage: StorageService, limit: int = _CLEANUP_BATCH
) -> int:
    """만료된 고아 세션을 정리한다(멀티파트 abort + 행 삭제). 정리 개수 반환.

    오브젝트는 절대 삭제하지 않는다(위 _discard 주석 참조). 전 과정 best-effort.
    """
    try:
        rows = list(
            (
                await session.execute(
                    select(UploadSession)
                    .where(UploadSession.expires_at < _now())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            try:
                await storage.abort_multipart_async(r.object_key, r.upload_id)
            except Exception:  # noqa: BLE001
                pass
        if rows:
            await session.execute(
                delete(UploadSession).where(
                    UploadSession.id.in_([r.id for r in rows])
                )
            )
            await session.commit()
        return len(rows)
    except Exception:  # noqa: BLE001
        await session.rollback()
        return 0


# --- 세션 개시 ---------------------------------------------------------------


async def init_new_upload(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    *,
    filename: str,
    parent_id: int | None,
    total_size: int,
    mime_type: str | None,
) -> UploadSession:
    """새 파일 재개 업로드 세션을 개시한다. 부모 write 권한 검사는 기존 업로드와 동일."""
    filename = (filename or "").strip()
    if not filename:
        raise FileServiceError(422, "파일 이름이 비어 있습니다.")
    if total_size > files_service.MAX_FILE_SIZE:
        raise FileServiceError(413, "파일 크기가 상한(10GB)을 초과했습니다.")

    parent = await files_service._resolve_parent(session, user, parent_id, need="write")
    # 할당량 소프트 사전검사(빠른 실패). 원자적 확정은 완료 시점에.
    if user.storage_used + total_size > user.max_storage:
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    mime = mime_type or "application/octet-stream"
    file_id = await _reserve_file_id(session)
    object_key = build_file_key(user.id, file_id)
    try:
        upload_id = await storage.create_multipart_async(object_key, mime)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise FileServiceError(502, "업로드 세션 개시에 실패했습니다.") from exc

    row = UploadSession(
        id=_new_token(),
        user_id=user.id,
        kind="new",
        file_id=file_id,
        parent_id=parent.id,
        object_key=object_key,
        upload_id=upload_id,
        filename=filename,
        mime_type=mime,
        total_size=total_size,
        part_size=settings.resumable_part_size,
        base_version=None,
        expires_at=_now() + timedelta(seconds=settings.resumable_session_ttl_seconds),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await cleanup_expired(session, storage)
    return row


async def init_version_upload(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    file_id: int,
    *,
    total_size: int,
    mime_type: str | None,
    base_version: int | None,
) -> UploadSession:
    """기존 파일 재업로드(새 버전) 재개 세션을 개시한다. write 권한 + 버전 충돌 검사."""
    file = await files_service.ensure_file_access(
        session, user, await files_service.get_file(session, file_id), need="write"
    )
    if file.is_folder:
        raise FileServiceError(400, "폴더는 재업로드할 수 없습니다.")
    if file.is_deleted:
        raise FileServiceError(409, "삭제된 파일은 재업로드할 수 없습니다.")
    if base_version is not None and base_version != file.current_version:
        raise FileServiceError(
            409,
            f"버전 충돌: 최신 버전은 v{file.current_version} 입니다 (요청 base v{base_version}).",
        )
    if total_size > files_service.MAX_FILE_SIZE:
        raise FileServiceError(413, "파일 크기가 상한(10GB)을 초과했습니다.")
    if user.storage_used + total_size > user.max_storage:
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    mime = mime_type or file.mime_type or "application/octet-stream"
    object_key = file.file_key
    try:
        upload_id = await storage.create_multipart_async(object_key, mime)
    except Exception as exc:  # noqa: BLE001
        raise FileServiceError(502, "업로드 세션 개시에 실패했습니다.") from exc

    row = UploadSession(
        id=_new_token(),
        user_id=user.id,
        kind="version",
        file_id=file.id,
        parent_id=None,
        object_key=object_key,
        upload_id=upload_id,
        filename=file.name,
        mime_type=mime,
        total_size=total_size,
        part_size=settings.resumable_part_size,
        base_version=base_version,
        expires_at=_now() + timedelta(seconds=settings.resumable_session_ttl_seconds),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await cleanup_expired(session, storage)
    return row


# --- 파트 업로드 / 상태 ------------------------------------------------------


async def upload_part(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    session_id: str,
    part_number: int,
    data: bytes,
) -> tuple[int, int, str]:
    """단일 파트를 스트리밍 저장한다. 같은 파트 재업로드는 덮어써 재시도를 지원한다.

    파트 크기를 정확히 검증해(마지막 파트 제외 part_size 고정) 완료 시 조립 크기가 선언
    크기와 정확히 일치하도록 강제한다. 반환: (part_number, size, etag).
    """
    row = await _load_session(session, user, session_id)
    total_parts = _total_parts(row.total_size, row.part_size)
    if part_number < 1 or part_number > total_parts:
        raise FileServiceError(422, f"파트 번호는 1..{total_parts} 범위여야 합니다.")
    expected = _expected_part_size(
        part_number, total_parts, row.total_size, row.part_size
    )
    if len(data) != expected:
        raise FileServiceError(
            422, f"파트 크기 불일치: 기대 {expected}바이트, 수신 {len(data)}바이트."
        )
    try:
        etag = await storage.upload_part_async(
            row.object_key, row.upload_id, part_number, data
        )
    except Exception as exc:  # noqa: BLE001
        raise FileServiceError(502, "파트 업로드에 실패했습니다.") from exc
    return part_number, len(data), etag


async def session_status(
    session: AsyncSession, storage: StorageService, user: User, session_id: str
) -> tuple[UploadSession, list[tuple[int, int, str]]]:
    """세션 + 스테이징된 파트 목록을 반환한다(재개용). parts=[(part_number, size, etag)]."""
    row = await _load_session(session, user, session_id)
    try:
        parts = await storage.list_parts_async(row.object_key, row.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise FileServiceError(502, "세션 상태 조회에 실패했습니다.") from exc
    return row, parts


async def abort_upload(
    session: AsyncSession, storage: StorageService, user: User, session_id: str
) -> None:
    """세션을 명시적으로 중단한다(멀티파트 abort + 세션 삭제). 이후 세션은 무효."""
    row = await _load_session(session, user, session_id)
    try:
        await storage.abort_multipart_async(row.object_key, row.upload_id)
    except Exception:  # noqa: BLE001
        pass
    await session.execute(delete(UploadSession).where(UploadSession.id == row.id))
    await session.commit()


# --- 완료 (파트 병합 → 파일/버전 경로 합류) ----------------------------------


async def complete_upload(
    session: AsyncSession, storage: StorageService, user: User, session_id: str
) -> File:
    """스테이징된 파트를 검증·병합해 파일을 확정한다. 새 파일/재업로드 경로로 분기."""
    row = await _load_session(session, user, session_id)
    total_parts = _total_parts(row.total_size, row.part_size)

    try:
        parts = await storage.list_parts_async(row.object_key, row.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise FileServiceError(502, "세션 상태 조회에 실패했습니다.") from exc

    present = {pn for pn, _s, _e in parts}
    expected = set(range(1, total_parts + 1))
    if present != expected:
        missing = sorted(expected - present)
        raise FileServiceError(
            409, f"업로드가 완료되지 않았습니다. 누락 파트: {missing[:20]}"
        )

    complete_parts = [(pn, etag) for pn, _size, etag in sorted(parts)]
    real_size = received_bytes(parts)

    if row.kind == "version":
        return await _complete_version(
            session, storage, user, row, complete_parts, real_size
        )
    return await _complete_new(session, storage, user, row, complete_parts, real_size)


async def _complete_new(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    row: UploadSession,
    complete_parts: list[tuple[int, str]],
    real_size: int,
) -> File:
    """새 파일 완료 — 기존 upload_file 의 DB 경로를 미리 선점한 file_id 로 재현한다."""
    # 롤백 후에도 안전하게 폐기할 수 있도록 세션 식별값을 미리 원시값으로 캡처한다.
    sid, object_key, upload_id = row.id, row.object_key, row.upload_id
    parent = await files_service._resolve_parent(
        session, user, row.parent_id, need="write"
    )
    # 할당량 원자적 확정(실제 저장 크기). 완료 tx 와 동일 — 실패 시 롤백이 선점을 취소한다.
    if not await files_service._reserve_quota(session, user.id, real_size):
        await session.rollback()
        await _discard(
            session, storage, session_id=sid, object_key=object_key, upload_id=upload_id
        )
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    file = File(
        id=row.file_id,
        user_id=user.id,
        group_id=None,
        parent_folder_id=parent.id,
        name=row.filename,
        file_key=row.object_key,
        mime_type=row.mime_type,
        size=real_size,
        is_folder=False,
        current_version=1,
    )
    session.add(file)
    session.add(
        FileVersion(
            file_id=row.file_id,
            version=1,
            object_key=row.object_key,
            size=real_size,
            mime_type=row.mime_type,
            uploaded_by=user.id,
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        await _discard(
            session, storage, session_id=sid, object_key=object_key, upload_id=upload_id
        )
        raise FileServiceError(409, "같은 이름의 파일이 이미 있습니다.") from exc

    # 파트 병합으로 오브젝트 확정 — commit 직전 마지막 위험 단계(upload_file 순서와 동일).
    try:
        await storage.complete_multipart_async(
            object_key, upload_id, complete_parts
        )
    except Exception as exc:  # noqa: BLE001
        # 병합 실패: 롤백으로 할당량 선점 취소, 세션은 유지해 재시도 가능하게 한다.
        await session.rollback()
        raise FileServiceError(502, "파트 병합에 실패했습니다.") from exc

    await session.execute(delete(UploadSession).where(UploadSession.id == sid))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await files_service._safe_delete_object(storage, object_key)
        await _discard(
            session, storage, session_id=sid, object_key=object_key, upload_id=upload_id
        )
        raise FileServiceError(409, "같은 이름의 파일이 이미 있습니다.") from exc

    await session.refresh(file)
    await thumbnails_service.maybe_generate(session, storage, file)
    # 썸네일 실패 시 내부 rollback 으로 세션 인스턴스가 expire 된다(files_service.revive 주석).
    await files_service.revive(session, user)
    await files_service.revive(session, file)
    await file_events_service.publish_file_event(
        type="upload",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=user.id,
        name=file.name,
    )
    return file


async def _complete_version(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    row: UploadSession,
    complete_parts: list[tuple[int, str]],
    real_size: int,
) -> File:
    """재업로드 완료 — 기존 재업로드와 동일한 _commit_new_version 경로에 합류한다."""
    sid, object_key, upload_id = row.id, row.object_key, row.upload_id
    file = await files_service.ensure_file_access(
        session, user, await files_service.get_file(session, row.file_id), need="write"
    )
    if file.is_folder or file.is_deleted:
        raise FileServiceError(409, "재업로드 대상 파일이 유효하지 않습니다.")
    # 개시 이후 다른 버전이 끼어들었는지 재확인(충돌 감지).
    if row.base_version is not None and row.base_version != file.current_version:
        raise FileServiceError(
            409, f"버전 충돌: 최신 버전은 v{file.current_version} 입니다."
        )

    if not await files_service._reserve_quota(session, user.id, real_size):
        await session.rollback()
        await _discard(
            session, storage, session_id=sid, object_key=object_key, upload_id=upload_id
        )
        raise FileServiceError(413, "저장 용량 할당량을 초과했습니다.")

    # 세션 행 삭제를 완료 tx 에 포함 — _commit_new_version 의 flush/commit 과 함께 반영되고,
    # 그 롤백 경로에서는 함께 취소돼 재시도가 가능하다.
    await session.execute(delete(UploadSession).where(UploadSession.id == sid))

    async def _merge(_prev_key: str) -> None:
        await storage.complete_multipart_async(object_key, upload_id, complete_parts)

    return await files_service._commit_new_version(
        session,
        storage,
        file,
        size=real_size,
        mime=row.mime_type or file.mime_type or "application/octet-stream",
        uploaded_by=user.id,
        write_new_content=_merge,
    )
