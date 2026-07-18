"""파일 서비스 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오: admin 부트스트랩 → alice 승인/로그인 → 10MB 업로드 → 목록/메타 → 다운로드
(X-Accel-Redirect 헤더 확인 + 해당 presigned URL 로 MinIO 직접 GET 바이트 일치) → 폴더 생성
→ 이름 변경 → 동명 409 → 소프트 삭제 → 휴지통 → 복구 → 영구 삭제(MinIO 오브젝트 제거 +
storage_used 감소) → 할당량 초과 413.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET. `python -m tests.integration_files`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport
from minio.error import S3Error
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import User
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
TEN_MB = 10 * 1024 * 1024
PAYLOAD = (b"MiniDrive-0123456789" * ((TEN_MB // 20) + 1))[:TEN_MB]


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
    # 이전 실행 잔여 오브젝트 정리.
    leftover = [
        o.object_name
        for o in storage_service._client.list_objects(
            storage_service.bucket, recursive=True
        )
    ]
    storage_service.delete_many(leftover)


async def _active_alice_headers(c: httpx.AsyncClient) -> tuple[dict[str, str], int]:
    # 셋업 위저드로 admin + 초기 코드 생성 → 코드로 alice 즉시 active 가입.
    _admin_h, code = await setup_admin(c)
    return await register_active(c, code, ALICE)


async def _storage_used(user_id: int) -> int:
    async with SessionFactory() as s:
        row = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        return row.storage_used


async def scenario() -> None:
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        alice_h, alice_id = await _active_alice_headers(c)
        _ok("셋업 + 코드 가입 → alice 로그인")

        # 1. 10MB 업로드 (루트)
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("hello.bin", PAYLOAD, "application/octet-stream")},
        )
        assert r.status_code == 201, r.text
        file_id = r.json()["id"]
        assert r.json()["size"] == TEN_MB
        assert r.json()["current_version"] == 1
        _ok("10MB 업로드 (201, size/current_version 확인)")

        # storage_used 반영
        assert await _storage_used(alice_id) == TEN_MB
        _ok("업로드 후 storage_used == 10MB")

        # 2. 목록 (루트)
        r = await c.get("/api/files", headers=alice_h)
        assert r.status_code == 200 and r.json()["total"] == 1, r.text
        assert r.json()["items"][0]["id"] == file_id
        _ok("목록 조회 (total=1)")

        # 3. 메타데이터
        r = await c.get(f"/api/files/{file_id}", headers=alice_h)
        assert r.status_code == 200 and r.json()["name"] == "hello.bin", r.text
        _ok("메타데이터 조회")

        # 4. 다운로드 — X-Accel-Redirect 헤더 확인
        r = await c.get(f"/api/files/{file_id}/download", headers=alice_h)
        assert r.status_code == 200, r.text
        accel = r.headers.get("X-Accel-Redirect")
        assert accel and accel.startswith("/_minio/"), accel
        assert "attachment" in r.headers.get("Content-Disposition", "")
        _ok(f"다운로드 X-Accel-Redirect 헤더 확인 ({accel[:40]}...)")

        # presigned URL 로 MinIO 직접 GET → 바이트 일치
        secure = "https" if settings.minio_secure else "http"
        minio_path = accel[len("/_minio") :]  # /minidrive/users/..?X-Amz-..
        minio_url = f"{secure}://{settings.minio_endpoint}{minio_path}"
        async with httpx.AsyncClient(timeout=60) as raw:
            got = await raw.get(minio_url)
        assert got.status_code == 200, f"{got.status_code} {got.text[:200]}"
        assert got.content == PAYLOAD, f"len={len(got.content)} vs {len(PAYLOAD)}"
        _ok("presigned URL 직접 GET → 바이트 일치 (10MB)")

        # 5. 폴더 생성
        r = await c.post("/api/files", headers=alice_h, json={"name": "docs"})
        assert r.status_code == 201 and r.json()["is_folder"] is True, r.text
        folder_id = r.json()["id"]
        _ok("폴더 생성")

        # 폴더 하위에 업로드되는지 (parent_id)
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            data={"parent_id": str(folder_id)},
            files={"file": ("inside.txt", b"nested", "text/plain")},
        )
        assert r.status_code == 201 and r.json()["parent_folder_id"] == folder_id, r.text
        nested_id = r.json()["id"]
        _ok("폴더 하위 업로드 (parent_id)")

        # 6. 이름 변경
        r = await c.put(f"/api/files/{file_id}", headers=alice_h, json={"name": "renamed.bin"})
        assert r.status_code == 200 and r.json()["name"] == "renamed.bin", r.text
        _ok("이름 변경")

        # 7. 동명 충돌 409 (docs 폴더 하나 더)
        r = await c.post("/api/files", headers=alice_h, json={"name": "docs"})
        assert r.status_code == 409, r.text
        _ok("동명 폴더 생성 409")

        # 이름 변경으로 동명 충돌
        r = await c.post("/api/files", headers=alice_h, json={"name": "temp"})
        temp_id = r.json()["id"]
        r = await c.put(f"/api/files/{temp_id}", headers=alice_h, json={"name": "docs"})
        assert r.status_code == 409, r.text
        _ok("이름 변경 동명 충돌 409")

        # 8. 소프트 삭제 (폴더 → 하위 재귀)
        r = await c.post(f"/api/files/{folder_id}/delete", headers=alice_h)
        assert r.status_code == 204, r.text
        # 목록에서 사라짐
        r = await c.get("/api/files", headers=alice_h)
        ids = [it["id"] for it in r.json()["items"]]
        assert folder_id not in ids
        _ok("폴더 소프트 삭제 (목록에서 제거)")

        # 9. 휴지통 — 최상위(folder)만, 하위(nested)는 제외
        r = await c.get("/api/files/trash", headers=alice_h)
        assert r.status_code == 200, r.text
        trash_ids = [it["id"] for it in r.json()]
        assert folder_id in trash_ids and nested_id not in trash_ids, trash_ids
        _ok("휴지통 최상위만 노출 (하위 재귀 항목 제외)")

        # 10. 복구
        r = await c.post(f"/api/files/{folder_id}/restore-trash", headers=alice_h)
        assert r.status_code == 200, r.text
        r = await c.get("/api/files/trash", headers=alice_h)
        assert folder_id not in [it["id"] for it in r.json()]
        # 하위도 함께 복구 — docs 폴더 목록에 nested 존재
        r = await c.get("/api/files", headers=alice_h, params={"parentId": folder_id})
        assert nested_id in [it["id"] for it in r.json()["items"]], r.text
        _ok("복구 (하위까지 되살아남)")

        # 11. 영구 삭제 — 휴지통으로 보낸 뒤 permanent-delete
        used_before = await _storage_used(alice_id)
        nested_key = f"users/{alice_id}/{nested_id}"
        # nested 파일 개별 소프트 삭제 후 영구 삭제
        r = await c.post(f"/api/files/{nested_id}/delete", headers=alice_h)
        assert r.status_code == 204, r.text
        r = await c.post(f"/api/files/{nested_id}/permanent-delete", headers=alice_h)
        assert r.status_code == 204, r.text
        # MinIO 오브젝트 제거 확인
        try:
            storage_service.stat(nested_key)
            raise AssertionError("nested 오브젝트가 아직 존재함")
        except S3Error as exc:
            assert exc.code in ("NoSuchKey", "NoSuchObject"), exc.code
        # storage_used 감소 (nested = 6 bytes)
        assert await _storage_used(alice_id) == used_before - len(b"nested")
        _ok("영구 삭제 (MinIO 오브젝트 제거 + storage_used 감소)")

        # 휴지통 아닌 항목 영구 삭제 거부 409
        r = await c.post(f"/api/files/{file_id}/permanent-delete", headers=alice_h)
        assert r.status_code == 409, r.text
        _ok("휴지통 아닌 항목 영구 삭제 거부 409")

        # 12. 할당량 초과 413 — max_storage 를 현재 사용량 근처로 낮추고 업로드
        async with SessionFactory() as s:
            row = (await s.execute(select(User).where(User.id == alice_id))).scalar_one()
            row.max_storage = row.storage_used + 5  # 5바이트 여유
            await s.commit()
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("big.bin", b"0123456789", "application/octet-stream")},
        )
        assert r.status_code == 413, r.text
        # 초과 업로드가 storage_used 를 늘리지 않았는지(롤백) 확인
        async with SessionFactory() as s:
            row = (await s.execute(select(User).where(User.id == alice_id))).scalar_one()
            assert row.storage_used <= row.max_storage
        _ok("할당량 초과 413 (선점 롤백 확인)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n파일 서비스 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
