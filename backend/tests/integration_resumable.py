"""재개 가능 업로드(S3 Multipart) 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

Phase 5-2 시나리오:
  - 새 파일 다청크 업로드 완료 → 내용 무결성(다운로드 바이트 일치), 목록 노출, 세션 소멸.
  - 중단 후 재개: 파트 조회 → 남은 파트만 올리고 완료.
  - 단일 파트(이미지) 완료 → 썸네일 best-effort 생성.
  - 재업로드(새 버전): base_version 충돌 감지, 완료 시 버저닝 경로 합류(v1 보존, 현재=신규).
  - 잘못된/타인 세션 접근 거부(404), 파트 크기 불일치 422, 미완료 완료 409.
  - 할당량 초과: 개시 소프트 거부(413) + 완료 원자적 거부(413) 후 세션 폐기.
  - abort 후 세션 무효(404).

env: DATABASE_URL / REDIS_URL / MINIO_* + RESUMABLE_PART_SIZE=5242880(테스트는 5MiB 청크).
실행: `python -m tests.integration_resumable`.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys

import httpx
from httpx import ASGITransport
from PIL import Image
from sqlalchemy import select, update

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import File, UploadSession, User
from app.services.storage import storage_service
from app.services.users import ensure_admin_bootstrap

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

PART = settings.resumable_part_size  # 테스트에서 5MiB 로 낮춰 다청크를 가볍게 검증


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _png_bytes(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (10, 120, 200))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
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


async def _register_approve_login(
    c: httpx.AsyncClient, admin_h: dict[str, str], who: dict[str, str]
) -> tuple[dict[str, str], int]:
    r = await c.post("/api/auth/register", json=who)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = await c.post(f"/api/admin/users/{uid}/approve", headers=admin_h)
    assert r.status_code == 200, r.text
    r = await c.post(
        "/api/auth/login", json={"email": who["email"], "password": who["password"]}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, uid


async def _admin_headers(c: httpx.AsyncClient) -> dict[str, str]:
    r = await c.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_initial_password},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _fetch_minio(accel: str) -> httpx.Response:
    secure = "https" if settings.minio_secure else "http"
    minio_path = accel[len("/_minio") :]
    url = f"{secure}://{settings.minio_endpoint}{minio_path}"
    async with httpx.AsyncClient(timeout=120) as raw:
        return await raw.get(url)


def _split(content: bytes) -> list[bytes]:
    """content 를 서버 part_size 기준으로 청크 리스트로 나눈다."""
    return [content[i : i + PART] for i in range(0, len(content), PART)]


async def _upload_all_parts(
    c: httpx.AsyncClient, h: dict[str, str], sid: str, parts: list[bytes]
) -> None:
    for n, chunk in enumerate(parts, start=1):
        r = await c.put(
            f"/api/files/uploads/{sid}/parts/{n}", headers=h, content=chunk
        )
        assert r.status_code == 200, r.text
        assert r.json()["size"] == len(chunk), r.text


async def _download_bytes(
    c: httpx.AsyncClient, h: dict[str, str], file_id: int
) -> bytes:
    r = await c.get(f"/api/files/{file_id}/download", headers=h)
    assert r.status_code == 200, r.text
    accel = r.headers["X-Accel-Redirect"]
    got = await _fetch_minio(accel)
    assert got.status_code == 200, got.status_code
    return got.content


async def _download_version_bytes(
    c: httpx.AsyncClient, h: dict[str, str], file_id: int, version: int
) -> bytes:
    r = await c.get(
        f"/api/files/{file_id}/versions/{version}/download", headers=h
    )
    assert r.status_code == 200, r.text
    got = await _fetch_minio(r.headers["X-Accel-Redirect"])
    assert got.status_code == 200, got.status_code
    return got.content


async def _set_max_storage(uid: int, value: int) -> None:
    async with SessionFactory() as s:
        await s.execute(update(User).where(User.id == uid).values(max_storage=value))
        await s.commit()


async def _storage_used(uid: int) -> int:
    async with SessionFactory() as s:
        return (
            await s.execute(select(User.storage_used).where(User.id == uid))
        ).scalar_one()


async def _session_exists(sid: str) -> bool:
    async with SessionFactory() as s:
        return (await s.get(UploadSession, sid)) is not None


async def scenario() -> None:
    assert PART >= 5 * 1024 * 1024, (
        f"멀티파트 검증엔 part_size>=5MiB 필요(현재 {PART}). "
        "RESUMABLE_PART_SIZE=5242880 로 실행하라."
    )
    await _reset()

    async with SessionFactory() as session:
        admin = await ensure_admin_bootstrap(
            session, settings.admin_email, settings.admin_initial_password
        )
    assert admin is not None
    _ok("admin 부트스트랩")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
        admin_h = await _admin_headers(c)
        alice_h, alice_id = await _register_approve_login(c, admin_h, ALICE)
        bob_h, bob_id = await _register_approve_login(c, admin_h, BOB)
        _ok("alice/bob 승인·로그인")

        # === 1. 새 파일 다청크 업로드 완료 + 무결성 ===
        content = os.urandom(PART + 4096)  # 2 파트 (5MiB + 4096B)
        chunks = _split(content)
        assert len(chunks) == 2
        r = await c.post(
            "/api/files/uploads",
            headers=alice_h,
            json={
                "filename": "big.bin",
                "total_size": len(content),
                "mime_type": "application/octet-stream",
            },
        )
        assert r.status_code == 201, r.text
        sess = r.json()
        sid = sess["session_id"]
        assert sess["part_size"] == PART, sess
        assert sess["total_parts"] == 2 and sess["uploaded_parts"] == [], sess
        big_file_id = sess["file_id"]
        _ok(f"세션 개시 (file_id={big_file_id}, total_parts=2)")

        await _upload_all_parts(c, alice_h, sid, chunks)
        r = await c.get(f"/api/files/uploads/{sid}", headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.json()["uploaded_parts"] == [1, 2], r.text
        assert r.json()["received_bytes"] == len(content), r.text
        _ok("2개 파트 업로드 + 상태 조회(uploaded_parts=[1,2])")

        r = await c.post(f"/api/files/uploads/{sid}/complete", headers=alice_h)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["id"] == big_file_id, created
        assert created["size"] == len(content), created
        assert created["current_version"] == 1, created
        _ok("완료 → 파일 확정 (size 일치, v1)")

        assert (await _download_bytes(c, alice_h, big_file_id)) == content
        _ok("다운로드 바이트 == 업로드 원본 (무결성)")

        # 목록 노출 + 세션 소멸
        r = await c.get("/api/files", headers=alice_h)
        assert any(f["id"] == big_file_id for f in r.json()["items"]), r.text
        r = await c.get(f"/api/files/uploads/{sid}", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("목록 노출 + 완료 후 세션 404")

        # storage_used 정확 반영
        assert (await _storage_used(alice_id)) == len(content)
        _ok(f"할당량 반영 (storage_used={len(content)})")

        # === 2. 중단 후 재개 ===
        content2 = os.urandom(PART + 100)
        chunks2 = _split(content2)
        r = await c.post(
            "/api/files/uploads",
            headers=alice_h,
            json={"filename": "resume.bin", "total_size": len(content2)},
        )
        sid2 = r.json()["session_id"]
        # 파트 1만 올리고 "중단"
        r = await c.put(
            f"/api/files/uploads/{sid2}/parts/1", headers=alice_h, content=chunks2[0]
        )
        assert r.status_code == 200, r.text
        # 재개: 상태 조회로 남은 파트 파악
        r = await c.get(f"/api/files/uploads/{sid2}", headers=alice_h)
        assert r.json()["uploaded_parts"] == [1], r.text
        # 남은 파트 2 업로드 후 완료
        r = await c.put(
            f"/api/files/uploads/{sid2}/parts/2", headers=alice_h, content=chunks2[1]
        )
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/files/uploads/{sid2}/complete", headers=alice_h)
        assert r.status_code == 201, r.text
        resume_id = r.json()["id"]
        assert (await _download_bytes(c, alice_h, resume_id)) == content2
        _ok("중단 후 재개 완료 + 무결성")

        # === 3. 단일 파트(이미지) → 썸네일 best-effort ===
        png = _png_bytes(800, 600)
        assert len(png) < PART  # 단일 파트(마지막 파트라 5MiB 미만 허용)
        r = await c.post(
            "/api/files/uploads",
            headers=alice_h,
            json={"filename": "photo.png", "total_size": len(png), "mime_type": "image/png"},
        )
        sid3 = r.json()["session_id"]
        assert r.json()["total_parts"] == 1, r.text
        r = await c.put(
            f"/api/files/uploads/{sid3}/parts/1", headers=alice_h, content=png
        )
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/files/uploads/{sid3}/complete", headers=alice_h)
        assert r.status_code == 201, r.text
        img_id = r.json()["id"]
        async with SessionFactory() as s:
            tkey = (
                await s.execute(select(File.thumbnail_key).where(File.id == img_id))
            ).scalar_one()
        assert tkey == f"thumbnails/{img_id}.png", tkey
        r = await c.get(f"/api/files/{img_id}/thumbnail", headers=alice_h)
        assert r.status_code == 200, r.text
        _ok("단일 파트 이미지 완료 → 썸네일 생성 + /thumbnail 200")

        # === 4. 재업로드(새 버전) — 버전 충돌 + 버저닝 합류 ===
        newv = os.urandom(PART + 2048)  # 2 파트
        newv_chunks = _split(newv)
        # 잘못된 base_version → 개시 시 409
        r = await c.post(
            f"/api/files/{big_file_id}/uploads",
            headers=alice_h,
            json={"total_size": len(newv), "base_version": 99},
        )
        assert r.status_code == 409, r.text
        _ok("재업로드 개시: 잘못된 base_version → 409")

        r = await c.post(
            f"/api/files/{big_file_id}/uploads",
            headers=alice_h,
            json={"total_size": len(newv), "base_version": 1},
        )
        assert r.status_code == 201, r.text
        vsid = r.json()["session_id"]
        assert r.json()["kind"] == "version" and r.json()["file_id"] == big_file_id
        await _upload_all_parts(c, alice_h, vsid, newv_chunks)
        r = await c.post(f"/api/files/uploads/{vsid}/complete", headers=alice_h)
        assert r.status_code == 201, r.text
        assert r.json()["current_version"] == 2, r.text
        assert r.json()["size"] == len(newv), r.text
        _ok("재업로드 완료 → current_version=2")

        # 버전 목록 v1/v2, 현재=신규, v1=이전 내용 보존
        r = await c.get(f"/api/files/{big_file_id}/versions", headers=alice_h)
        versions = {v["version"] for v in r.json()["items"]}
        assert versions == {1, 2}, r.json()
        assert (await _download_bytes(c, alice_h, big_file_id)) == newv
        assert (await _download_version_bytes(c, alice_h, big_file_id, 1)) == content
        _ok("버전 불변식: 현재=신규 내용, v1=이전 내용 보존")

        # === 5. 미완료 완료 → 409 ===
        c5 = os.urandom(PART + 10)
        c5_chunks = _split(c5)
        r = await c.post(
            "/api/files/uploads",
            headers=alice_h,
            json={"filename": "incomplete.bin", "total_size": len(c5)},
        )
        sid5 = r.json()["session_id"]
        await c.put(
            f"/api/files/uploads/{sid5}/parts/1", headers=alice_h, content=c5_chunks[0]
        )
        r = await c.post(f"/api/files/uploads/{sid5}/complete", headers=alice_h)
        assert r.status_code == 409, r.text
        _ok("미완료(파트 누락) 완료 → 409")
        await c.delete(f"/api/files/uploads/{sid5}", headers=alice_h)

        # === 6. 파트 크기 불일치 → 422 ===
        r = await c.post(
            "/api/files/uploads",
            headers=alice_h,
            json={"filename": "badsize.bin", "total_size": PART + 500},
        )
        sid6 = r.json()["session_id"]
        r = await c.put(
            f"/api/files/uploads/{sid6}/parts/1",
            headers=alice_h,
            content=os.urandom(PART - 10),  # part_size 와 불일치
        )
        assert r.status_code == 422, r.text
        _ok("파트 크기 불일치 → 422")
        await c.delete(f"/api/files/uploads/{sid6}", headers=alice_h)

        # === 7. 타인/무효 세션 접근 거부 (404) ===
        r = await c.post(
            "/api/files/uploads",
            headers=alice_h,
            json={"filename": "guarded.bin", "total_size": len(png)},
        )
        guarded = r.json()["session_id"]
        for method, path in (
            ("GET", f"/api/files/uploads/{guarded}"),
            ("POST", f"/api/files/uploads/{guarded}/complete"),
            ("DELETE", f"/api/files/uploads/{guarded}"),
        ):
            r = await c.request(method, path, headers=bob_h)
            assert r.status_code == 404, f"bob {method} {path} -> {r.status_code}"
        r = await c.put(
            f"/api/files/uploads/{guarded}/parts/1", headers=bob_h, content=png
        )
        assert r.status_code == 404, r.text
        # 무효 세션 id
        r = await c.get("/api/files/uploads/nonexistent-xyz", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("타인 세션·무효 세션 접근 404")
        await c.delete(f"/api/files/uploads/{guarded}", headers=alice_h)

        # === 8. 할당량: 개시 소프트 거부 ===
        await _set_max_storage(bob_id, 1024)  # 1KiB 로 축소
        r = await c.post(
            "/api/files/uploads",
            headers=bob_h,
            json={"filename": "toobig.bin", "total_size": PART + 1},
        )
        assert r.status_code == 413, r.text
        _ok("할당량 개시 소프트 거부 → 413")

        # === 9. 할당량: 완료 원자적 거부 + 세션 폐기 ===
        await _set_max_storage(bob_id, 10 * 1024 * 1024 * 1024)  # 넉넉히 복원
        small = os.urandom(4096)  # 단일 파트
        r = await c.post(
            "/api/files/uploads",
            headers=bob_h,
            json={"filename": "q.bin", "total_size": len(small)},
        )
        qsid = r.json()["session_id"]
        await c.put(
            f"/api/files/uploads/{qsid}/parts/1", headers=bob_h, content=small
        )
        # 개시 후 max_storage 를 축소해 완료 시점 원자적 거부를 유도
        await _set_max_storage(bob_id, 100)
        r = await c.post(f"/api/files/uploads/{qsid}/complete", headers=bob_h)
        assert r.status_code == 413, r.text
        assert not await _session_exists(qsid), "할당량 초과 완료 후 세션이 남음"
        assert (await _storage_used(bob_id)) == 0, "선점이 롤백되지 않음"
        _ok("완료 원자적 할당량 거부 → 413 + 세션 폐기 + 선점 롤백")

        # === 10. abort 후 세션 무효 ===
        await _set_max_storage(bob_id, 10 * 1024 * 1024 * 1024)
        r = await c.post(
            "/api/files/uploads",
            headers=bob_h,
            json={"filename": "abort.bin", "total_size": PART + 7},
        )
        asid = r.json()["session_id"]
        await c.put(
            f"/api/files/uploads/{asid}/parts/1",
            headers=bob_h,
            content=os.urandom(PART),
        )
        r = await c.delete(f"/api/files/uploads/{asid}", headers=bob_h)
        assert r.status_code == 204, r.text
        assert not await _session_exists(asid)
        r = await c.get(f"/api/files/uploads/{asid}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("abort → 204 + 세션 무효(404)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n재개 가능 업로드 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
