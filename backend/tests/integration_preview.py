"""썸네일/미리보기/공유 접근 통계 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

Phase 5-1 시나리오:
  - 이미지 업로드 → 썸네일 자동 생성(thumbnails/{fileId}.png), /thumbnail 게이트웨이 인라인
    스트리밍, presigned URL 로 MinIO 직접 GET → 실제 PNG(≤320px) 확인.
  - 비이미지 업로드 → 썸네일 미생성(/thumbnail 404). 이미지→텍스트 재업로드 시 이전 썸네일 제거.
  - 미리보기: 이미지/PDF 게이트웨이 인라인 redirect, 텍스트 인라인 본문(+1MiB 초과 truncated),
    미지원(zip) 415(구조화 detail), 폴더 400.
  - 권한: 소유자 아닌 사용자/admin 은 썸네일·미리보기 404(파일 내용 접근 불가).
  - 공유 접근 통계: 공개 메타/미리보기/다운로드가 view_count·last_access 를 기록하고, 목록/단건
    통계 API 로 조회된다. 미리보기는 download_count 를 소모하지 않는다.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.
실행: `python -m tests.integration_preview`.
"""

from __future__ import annotations

import asyncio
import io
import sys

import httpx
from httpx import ASGITransport
from PIL import Image
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import File
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _png_bytes(w: int, h: int) -> bytes:
    """실제 디코딩 가능한 PNG 바이트를 만든다(썸네일 생성이 Pillow 로 디코딩해야 하므로)."""
    img = Image.new("RGB", (w, h), (10, 120, 200))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()
    if not storage_service._client.bucket_exists(storage_service.bucket):
        storage_service._client.make_bucket(storage_service.bucket)
    leftover = [
        o.object_name
        for o in storage_service._client.list_objects(
            storage_service.bucket, recursive=True
        )
    ]
    storage_service.delete_many(leftover)


async def _thumbnail_key(file_id: int) -> str | None:
    async with SessionFactory() as s:
        row = (await s.execute(select(File).where(File.id == file_id))).scalar_one()
        return row.thumbnail_key


async def _fetch_minio(accel: str) -> httpx.Response:
    """X-Accel-Redirect(/_minio/...) 를 실제 MinIO presigned URL 로 바꿔 직접 GET 한다."""
    secure = "https" if settings.minio_secure else "http"
    minio_path = accel[len("/_minio") :]
    url = f"{secure}://{settings.minio_endpoint}{minio_path}"
    async with httpx.AsyncClient(timeout=60) as raw:
        return await raw.get(url)


async def scenario() -> None:
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        admin_h, code = await setup_admin(c)
        alice_h, _alice_id = await register_active(c, code, ALICE)
        bob_h, _bob_id = await register_active(c, code, BOB)
        _ok("셋업 + 코드 가입 → alice/bob 로그인")

        # === 1. 이미지 업로드 → 썸네일 자동 생성 ===
        png = _png_bytes(800, 600)
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("photo.png", png, "image/png")},
        )
        assert r.status_code == 201, r.text
        img_id = r.json()["id"]
        # 썸네일은 동기 best-effort 로 업로드 요청 내에서 생성됨.
        tkey = await _thumbnail_key(img_id)
        assert tkey == f"thumbnails/{img_id}.png", tkey
        _ok(f"이미지 업로드 → thumbnail_key={tkey}")

        # /thumbnail 게이트웨이 인라인 응답
        r = await c.get(f"/api/files/{img_id}/thumbnail", headers=alice_h)
        assert r.status_code == 200, r.text
        accel = r.headers.get("X-Accel-Redirect")
        assert accel and accel.startswith("/_minio/"), accel
        assert "inline" in r.headers.get("Content-Disposition", "")
        assert r.headers.get("Content-Type") == "image/png"
        _ok("GET /thumbnail → 200 X-Accel-Redirect inline image/png")

        # presigned URL 로 직접 GET → 실제 PNG, 320px 이내
        got = await _fetch_minio(accel)
        assert got.status_code == 200, got.status_code
        assert got.content[:8] == b"\x89PNG\r\n\x1a\n", "PNG 시그니처 불일치"
        thumb = Image.open(io.BytesIO(got.content))
        assert max(thumb.size) <= 320, thumb.size
        _ok(f"썸네일 오브젝트 실제 PNG (size={thumb.size}, ≤320px)")

        # === 2. 이미지 미리보기 (게이트웨이 인라인 redirect) ===
        r = await c.get(f"/api/files/{img_id}/preview", headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.headers.get("X-Accel-Redirect", "").startswith("/_minio/"), r.headers
        assert r.headers.get("Content-Type") == "image/png"
        assert "inline" in r.headers.get("Content-Disposition", "")
        _ok("이미지 GET /preview → 200 인라인 redirect")

        # === 3. 텍스트 미리보기 (인라인 본문) ===
        text_body = b"hello mini-drive preview\n" * 3
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("note.txt", text_body, "text/plain")},
        )
        assert r.status_code == 201, r.text
        txt_id = r.json()["id"]
        # 비이미지 → 썸네일 미생성
        assert await _thumbnail_key(txt_id) is None
        r = await c.get(f"/api/files/{txt_id}/thumbnail", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("텍스트 업로드 → 썸네일 미생성, /thumbnail 404")

        r = await c.get(f"/api/files/{txt_id}/preview", headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.headers.get("Content-Type", "").startswith("text/plain")
        assert r.content == text_body, r.content[:80]
        assert r.headers.get("X-Preview-Truncated") == "false"
        _ok("텍스트 GET /preview → 200 인라인 본문 (truncated=false)")

        # 1MiB 초과 텍스트 → 앞부분만 + truncated=true
        big = b"A" * (1024 * 1024 + 4096)
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("big.txt", big, "text/plain")},
        )
        big_id = r.json()["id"]
        r = await c.get(f"/api/files/{big_id}/preview", headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.headers.get("X-Preview-Truncated") == "true"
        assert len(r.content) == 1024 * 1024, len(r.content)
        _ok("1MiB 초과 텍스트 → truncated=true, 본문 정확히 1MiB")

        # === 4. PDF 미리보기 (mime 기반 인라인 redirect) ===
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("doc.pdf", b"%PDF-1.4 minimal", "application/pdf")},
        )
        pdf_id = r.json()["id"]
        r = await c.get(f"/api/files/{pdf_id}/preview", headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.headers.get("Content-Type") == "application/pdf"
        assert r.headers.get("X-Accel-Redirect", "").startswith("/_minio/")
        _ok("PDF GET /preview → 200 인라인 redirect (application/pdf)")

        # === 5. 미지원 타입 415 (zip) ===
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("a.zip", b"PK\x03\x04zipbytes", "application/zip")},
        )
        zip_id = r.json()["id"]
        r = await c.get(f"/api/files/{zip_id}/preview", headers=alice_h)
        assert r.status_code == 415, r.text
        detail = r.json()["detail"]
        assert detail["reason"] == "unsupported_preview", detail
        assert detail["mime_type"] == "application/zip", detail
        _ok("zip GET /preview → 415 (구조화 detail: unsupported_preview)")

        # === 6. 폴더 미리보기 400 ===
        r = await c.post("/api/files", headers=alice_h, json={"name": "folder"})
        folder_id = r.json()["id"]
        r = await c.get(f"/api/files/{folder_id}/preview", headers=alice_h)
        assert r.status_code == 400, r.text
        _ok("폴더 GET /preview → 400")

        # === 7. 권한: bob(미소유)·admin 은 썸네일/미리보기 404 (내용 접근 불가) ===
        for label, h in (("bob", bob_h), ("admin", admin_h)):
            r = await c.get(f"/api/files/{img_id}/thumbnail", headers=h)
            assert r.status_code == 404, f"{label} thumbnail {r.status_code}"
            r = await c.get(f"/api/files/{img_id}/preview", headers=h)
            assert r.status_code == 404, f"{label} preview {r.status_code}"
        _ok("bob·admin 썸네일/미리보기 404 (파일 내용 접근 불가, admin 우회 없음)")

        # === 8. 이미지→텍스트 재업로드 시 이전 썸네일 제거 ===
        r = await c.post(
            f"/api/files/{img_id}/upload",
            headers=alice_h,
            files={"file": ("photo.png", b"now plain text", "text/plain")},
        )
        assert r.status_code == 201, r.text
        assert await _thumbnail_key(img_id) is None, "재업로드 후 thumbnail_key 가 남음"
        r = await c.get(f"/api/files/{img_id}/thumbnail", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("이미지→텍스트 재업로드 → 이전 썸네일 제거 (/thumbnail 404)")

        # === 9. 공유 접근 통계 ===
        # 통계용으로 txt_id 를 공유(다운로드 권한).
        r = await c.post(
            "/api/shares",
            headers=alice_h,
            json={"file_id": txt_id, "permission": "download", "max_downloads": 5},
        )
        assert r.status_code == 201, r.text
        share = r.json()
        share_id = share["id"]
        share_url = share["share_url"]
        assert share["view_count"] == 0 and share["last_access_at"] is None
        _ok("공유 생성 (초기 view_count=0)")

        # 공개 메타 조회 → view 1건 기록
        r = await c.get(f"/api/public/shares/{share_url}")
        assert r.status_code == 200, r.text
        # 공개 미리보기 → view 1건 더 (download_count 는 불변)
        r = await c.post(f"/api/public/shares/{share_url}/preview")
        assert r.status_code == 200, r.text
        assert r.content == text_body, r.content[:80]
        _ok("공개 메타 + 공개 미리보기 (미리보기 본문 일치)")

        # 목록에 통계 반영 (view_count>=2, last_access 존재)
        r = await c.get("/api/shares", headers=alice_h)
        assert r.status_code == 200, r.text
        entry = next(s for s in r.json()["items"] if s["id"] == share_id)
        assert entry["view_count"] >= 2, entry
        assert entry["last_access_at"] is not None, entry
        assert entry["download_count"] == 0, entry  # 미리보기는 다운로드 소모 안 함
        _ok(f"공유 목록 통계 (view_count={entry['view_count']}, download_count=0)")

        # 단건 통계 API
        r = await c.get(f"/api/shares/{share_id}/stats", headers=alice_h)
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["share_id"] == share_id and st["view_count"] >= 2, st
        assert st["download_count"] == 0, st
        _ok("단건 통계 API (/api/shares/{id}/stats)")

        # 공개 다운로드 → download_count 증가 + view 기록
        r = await c.post(f"/api/public/shares/{share_url}/download")
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/shares/{share_id}/stats", headers=alice_h)
        st = r.json()
        assert st["download_count"] == 1, st
        assert st["view_count"] >= 3, st
        _ok("공개 다운로드 → download_count=1, view 추가 기록")

        # 남의 공유 통계 조회는 404 (소유자만)
        r = await c.get(f"/api/shares/{share_id}/stats", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("타인 공유 통계 조회 404 (소유자 전용)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n썸네일/미리보기/접근 통계 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
