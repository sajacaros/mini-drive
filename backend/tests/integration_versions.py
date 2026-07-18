"""버전 관리 + 다운로드 티켓 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오 (PRD 3.3, 6.2):
  업로드(v1) → 재업로드(v2, base_version 일치) → 충돌 409(잘못된 base_version)
  → 강제 재업로드(v3, base_version 미전달) → 버전 목록(최신순, 업로더 표시명, current 표시)
  → v1 다운로드(원래 바이트 일치) → v2 다운로드(v2 바이트 일치)
  → v1 복구(v4 생성, 바이트=v1) → storage_used == 모든 버전 크기 합계
  → 티켓 발급→GET 성공→재사용 실패 → 버전 티켓 발급→GET 성공
  → 영구 삭제 후 모든 오브젝트 제거 + 할당량 0.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.
실행: `python -m tests.integration_versions`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport
from minio.error import S3Error
from sqlalchemy import func, select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import FileVersion, User
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}

V1 = b"VERSION-ONE-" * 1000       # 12000 bytes
V2 = b"VERSION-TWO-CONTENT-" * 800  # 16000 bytes
V3 = b"v3" * 500                   # 1000 bytes


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


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


async def _active_alice_headers(c: httpx.AsyncClient) -> tuple[dict[str, str], int]:
    _admin_h, code = await setup_admin(c)
    return await register_active(c, code, ALICE)


async def _storage_used(user_id: int) -> int:
    async with SessionFactory() as s:
        row = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        return row.storage_used


async def _version_size_sum(file_id: int) -> int:
    async with SessionFactory() as s:
        return (
            await s.execute(
                select(func.coalesce(func.sum(FileVersion.size), 0)).where(
                    FileVersion.file_id == file_id
                )
            )
        ).scalar_one()


async def _minio_get(accel: str) -> bytes:
    secure = "https" if settings.minio_secure else "http"
    minio_url = f"{secure}://{settings.minio_endpoint}{accel[len('/_minio'):]}"
    async with httpx.AsyncClient(timeout=60) as raw:
        got = await raw.get(minio_url)
    assert got.status_code == 200, f"{got.status_code} {got.text[:200]}"
    return got.content


async def scenario() -> None:
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        alice_h, alice_id = await _active_alice_headers(c)
        _ok("셋업 + 코드 가입 → alice 로그인")

        # 1. v1 업로드
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("doc.bin", V1, "application/octet-stream")},
        )
        assert r.status_code == 201, r.text
        file_id = r.json()["id"]
        assert r.json()["current_version"] == 1 and r.json()["size"] == len(V1)
        _ok("v1 업로드 (current_version=1)")
        assert await _storage_used(alice_id) == len(V1)

        # 2. 재업로드 = v2 (base_version 일치)
        r = await c.post(
            f"/api/files/{file_id}/upload",
            headers=alice_h,
            data={"base_version": "1"},
            files={"file": ("doc.bin", V2, "text/plain")},
        )
        assert r.status_code == 201, r.text
        assert r.json()["current_version"] == 2, r.text
        assert r.json()["size"] == len(V2)
        assert r.json()["mime_type"] == "text/plain"
        _ok("재업로드 v2 (base_version 일치, size/mime 갱신)")
        # storage_used == v1 + v2 (스냅샷 보존)
        assert await _storage_used(alice_id) == len(V1) + len(V2), await _storage_used(alice_id)
        _ok("v2 후 storage_used == v1+v2 (스냅샷 점유)")

        # 3. 충돌 감지 — 잘못된 base_version → 409
        r = await c.post(
            f"/api/files/{file_id}/upload",
            headers=alice_h,
            data={"base_version": "1"},  # 실제 현재는 2
            files={"file": ("doc.bin", b"conflict", "text/plain")},
        )
        assert r.status_code == 409, r.text
        _ok("base_version 불일치 409 (충돌 감지)")
        # 실패한 재업로드가 storage_used 를 늘리지 않았는지(롤백)
        assert await _storage_used(alice_id) == len(V1) + len(V2)

        # 4. 강제 재업로드 v3 (base_version 미전달)
        r = await c.post(
            f"/api/files/{file_id}/upload",
            headers=alice_h,
            files={"file": ("doc.bin", V3, "application/octet-stream")},
        )
        assert r.status_code == 201 and r.json()["current_version"] == 3, r.text
        _ok("강제 재업로드 v3 (base_version 미전달)")

        # 5. 버전 목록 — 최신순, current 표시, 업로더 표시명
        r = await c.get(f"/api/files/{file_id}/versions", headers=alice_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["current_version"] == 3
        versions = [it["version"] for it in body["items"]]
        assert versions == [3, 2, 1], versions
        assert body["items"][0]["is_current"] is True
        assert all(it["uploaded_by_name"] == "Alice" for it in body["items"])
        sizes = {it["version"]: it["size"] for it in body["items"]}
        assert sizes == {1: len(V1), 2: len(V2), 3: len(V3)}, sizes
        _ok("버전 목록 (최신순, current 표시, 업로더 표시명, 크기)")

        # 6. v1 다운로드 → 원래 바이트 일치
        r = await c.get(f"/api/files/{file_id}/versions/1/download", headers=alice_h)
        assert r.status_code == 200, r.text
        accel_v1 = r.headers["X-Accel-Redirect"]
        assert "(v1)" in r.headers.get("Content-Disposition", ""), r.headers
        assert await _minio_get(accel_v1) == V1
        _ok("v1 다운로드 바이트 일치 (스냅샷)")

        # 7. v2 다운로드 → v2 바이트 일치
        r = await c.get(f"/api/files/{file_id}/versions/2/download", headers=alice_h)
        assert r.status_code == 200, r.text
        assert await _minio_get(r.headers["X-Accel-Redirect"]) == V2
        _ok("v2 다운로드 바이트 일치 (스냅샷)")

        # 현재(v3) 다운로드 = file_key
        r = await c.get(f"/api/files/{file_id}/download", headers=alice_h)
        assert await _minio_get(r.headers["X-Accel-Redirect"]) == V3
        _ok("현재(v3) 다운로드 바이트 일치")

        # 없는 버전 404
        r = await c.get(f"/api/files/{file_id}/versions/99/download", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("없는 버전 다운로드 404")

        # 8. v1 복구 → v4 생성, 바이트=v1
        r = await c.post(f"/api/files/{file_id}/versions/1/restore", headers=alice_h)
        assert r.status_code == 200 and r.json()["current_version"] == 4, r.text
        assert r.json()["size"] == len(V1)
        _ok("v1 복구 → v4 생성 (size=v1)")
        # 복구 후 현재 다운로드 == V1
        r = await c.get(f"/api/files/{file_id}/download", headers=alice_h)
        assert await _minio_get(r.headers["X-Accel-Redirect"]) == V1
        _ok("복구 후 현재 다운로드 바이트 == v1")
        # 원본 v1 스냅샷 여전히 접근 가능
        r = await c.get(f"/api/files/{file_id}/versions/1/download", headers=alice_h)
        assert await _minio_get(r.headers["X-Accel-Redirect"]) == V1
        _ok("복구 후에도 v1 스냅샷 보존 (이력 유실 0%)")

        # 9. storage_used == 모든 버전 크기 합계
        used = await _storage_used(alice_id)
        vsum = await _version_size_sum(file_id)
        assert used == vsum, f"used={used} vsum={vsum}"
        assert used == len(V1) + len(V2) + len(V3) + len(V1)
        _ok(f"storage_used == 버전 크기 합계 ({used})")

        # 10. 티켓 다운로드 — 발급 → GET 성공 → 재사용 실패
        r = await c.post(f"/api/files/{file_id}/download-ticket", headers=alice_h)
        assert r.status_code == 200, r.text
        ticket_url = r.json()["url"]
        assert r.json()["expires_in"] == 60
        # 무헤더 GET
        r = await c.get(ticket_url)
        assert r.status_code == 200, r.text
        assert await _minio_get(r.headers["X-Accel-Redirect"]) == V1  # 현재=v4(=v1 바이트)
        _ok("티켓 발급 → 무헤더 GET 성공")
        # 재사용 실패 (1회용)
        r = await c.get(ticket_url)
        assert r.status_code == 404, r.text
        _ok("티켓 재사용 404 (일회성)")
        # 무효 티켓 404
        r = await c.get("/api/files/download", params={"ticket": "bogus"})
        assert r.status_code == 404, r.text
        _ok("무효 티켓 404")

        # 버전 티켓 — v2
        r = await c.post(
            f"/api/files/{file_id}/versions/2/download-ticket", headers=alice_h
        )
        assert r.status_code == 200, r.text
        r = await c.get(r.json()["url"])
        assert r.status_code == 200, r.text
        assert await _minio_get(r.headers["X-Accel-Redirect"]) == V2
        _ok("버전 티켓 발급 → GET 성공 (v2 바이트)")

        # 11. 영구 삭제 → 모든 오브젝트 제거 + 할당량 0
        # 오브젝트 키 수집 (원본 + 스냅샷 v1..v3)
        expected_keys = [f"users/{alice_id}/{file_id}"] + [
            f"versions/{file_id}/v{n}" for n in (1, 2, 3)
        ]
        r = await c.post(f"/api/files/{file_id}/delete", headers=alice_h)
        assert r.status_code == 204, r.text
        r = await c.post(f"/api/files/{file_id}/permanent-delete", headers=alice_h)
        assert r.status_code == 204, r.text
        for key in expected_keys:
            try:
                storage_service.stat(key)
                raise AssertionError(f"오브젝트가 아직 존재함: {key}")
            except S3Error as exc:
                assert exc.code in ("NoSuchKey", "NoSuchObject"), f"{key}: {exc.code}"
        _ok("영구 삭제 → 원본+모든 스냅샷 오브젝트 제거")
        # 버킷에 잔여 오브젝트 없음
        leftover = [
            o.object_name
            for o in storage_service._client.list_objects(
                storage_service.bucket, recursive=True
            )
        ]
        assert leftover == [], leftover
        assert await _storage_used(alice_id) == 0, await _storage_used(alice_id)
        _ok("영구 삭제 → storage_used == 0 (할당량 완전 회수)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n버전 관리 + 티켓 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
