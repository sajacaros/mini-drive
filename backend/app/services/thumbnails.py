"""썸네일 자동 생성 서비스 (PRD 3.2, 2.1 — thumbnails/{fileId}.png).

이미지(mime `image/*`) 파일이 업로드·재업로드·복구될 때 최대 320px(비율 유지) PNG 썸네일을
생성해 `thumbnails/{fileId}.png` 오브젝트로 저장하고 files.thumbnail_key 를 갱신한다.

설계 결정:
  - **best-effort**: 썸네일 생성/저장 실패는 업로드를 절대 막지 않는다(경고 로그만). 원본 업로드가
    이미 커밋된 뒤에 별도로 수행되므로, 여기서 예외가 나도 파일/버전/할당량은 그대로 유효하다.
  - **동기 처리**: 사내 규모에서는 업로드 요청 내 동기 생성으로 충분하다(작업 큐 미도입). Pillow
    디코딩은 CPU 바운드라 `asyncio.to_thread` 로 이벤트 루프를 막지 않는다.
  - **큰 이미지 방어**: 원본이 50MB 를 초과하면 스킵한다(backend 로 전체 적재 회피). 픽셀 수는
    Pillow 의 기본 `MAX_IMAGE_PIXELS`(약 1.78억 px) 방어를 그대로 사용해 decompression bomb 을
    막는다 — 초과 시 예외가 나고 best-effort 로 무시된다.
  - 이미지가 아니거나 스킵 조건이면, 남아 있던 이전 썸네일(재업로드로 image→비image 전환 등)을
    제거해 stale 썸네일이 남지 않게 한다.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import File
from app.services.storage import StorageService

_log = get_logger("app.thumbnails")

# 썸네일 최대 변(px) — 비율 유지 축소 (PRD 3.2). 가로/세로 중 긴 변 기준.
THUMBNAIL_MAX_DIM = 320

# 원본 크기 상한 — 초과 시 썸네일 생성을 스킵한다(backend 메모리 방어).
MAX_THUMBNAIL_SOURCE_BYTES = 50 * 1024 * 1024

_THUMBNAIL_MIME = "image/png"


# image/ 로 시작하지만 PIL(래스터 디코더)이 열 수 없는 타입 — 시도해도 반드시 실패한다.
# SVG 는 벡터 포맷이라 별도 렌더러(cairosvg 등)가 필요하다. 미지원을 선언해 두면 원본을
# 스토리지에서 내려받는 헛수고와 확정된 예외를 아낀다.
_NON_RASTER_IMAGE_MIMES = frozenset({"image/svg+xml", "image/svg"})


def is_thumbnailable(mime_type: str | None) -> bool:
    """썸네일 생성 대상인지. `image/` 계열이되 PIL 이 못 읽는 벡터 포맷은 제외한다."""
    if not mime_type:
        return False
    return mime_type.startswith("image/") and mime_type not in _NON_RASTER_IMAGE_MIMES


def build_thumbnail_key(file_id: int) -> str:
    """썸네일 오브젝트 키 (PRD 2.1): thumbnails/{fileId}.png."""
    return f"thumbnails/{file_id}.png"


def render_png(data: bytes) -> bytes:
    """원본 이미지 바이트를 최대 320px PNG 썸네일 바이트로 변환한다.

    EXIF 회전 정보를 반영하고, 알파가 있으면 RGBA, 없으면 RGB 로 정규화해 PNG 로 저장한다.
    디코딩 불가/픽셀 초과 등은 Pillow 예외를 그대로 올려 호출자가 best-effort 로 처리한다.
    """
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image) or image
        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        image = image.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((THUMBNAIL_MAX_DIM, THUMBNAIL_MAX_DIM))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


async def maybe_generate(
    session: AsyncSession, storage: StorageService, file: File
) -> None:
    """파일의 현재 원본으로 썸네일을 생성/갱신한다(best-effort). 원본 커밋 이후 호출한다.

    이미지가 아니거나 크기 초과면 스킵하고, 남아 있던 이전 썸네일은 제거한다. 어떤 실패도
    호출자(업로드/복구 흐름)로 전파하지 않는다 — 경고 로그만 남긴다.
    """
    # rollback 은 세션의 ORM 인스턴스를 expire 시키므로, 그 뒤 file.id 를 읽으면 지연 로드가
    # 일어나 MissingGreenlet 으로 죽는다. "실패해도 전파하지 않는다"는 이 함수의 약속이
    # 정작 실패 처리 자체에서 깨지던 원인이라, 로그에 쓸 값은 미리 잡아 둔다.
    file_id = file.id
    try:
        if file.is_folder or not is_thumbnailable(file.mime_type):
            await _clear_thumbnail(session, storage, file)
            return
        if file.size > MAX_THUMBNAIL_SOURCE_BYTES:
            _log.info("thumbnail_skip_large", file_id=file_id, size=file.size)
            await _clear_thumbnail(session, storage, file)
            return

        data = await storage.get_bytes_async(file.file_key)
        png = render_png(data)

        key = build_thumbnail_key(file.id)
        await storage.put_async(key, io.BytesIO(png), len(png), _THUMBNAIL_MIME)
        file.thumbnail_key = key
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort, 업로드를 막지 않는다.
        await session.rollback()
        _log.warning("thumbnail_generate_failed", file_id=file_id, error=str(exc))
    finally:
        # commit/rollback 으로 만료된 ORM 속성을 다시 로드해, 호출자가 file 을 그대로 직렬화해도
        # 지연 IO(MissingGreenlet)가 나지 않게 한다. 실패는 무시(best-effort).
        try:
            await session.refresh(file)
        except Exception:  # noqa: BLE001
            pass


async def _clear_thumbnail(
    session: AsyncSession, storage: StorageService, file: File
) -> None:
    """이전에 만들어진 썸네일이 있으면 오브젝트와 thumbnail_key 를 제거한다(best-effort)."""
    if not file.thumbnail_key:
        return
    stale_key = file.thumbnail_key
    file.thumbnail_key = None
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        return
    try:
        await storage.delete_async(stale_key)
    except Exception:  # noqa: BLE001
        pass
