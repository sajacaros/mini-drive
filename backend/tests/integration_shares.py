"""공유 링크 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오: admin 부트스트랩 → alice 승인/로그인 → 업로드 → 공유 생성 → 무인증 메타 조회 →
무인증 다운로드(X-Accel-Redirect + presigned URL 로 MinIO 직접 GET 바이트 일치) →
비밀번호 공유(오답 401 / 정답 200) → 만료 공유 410(메타·다운로드) →
max_downloads 소진 410 → 폴더 공유 400 → 없는 URL 404 →
비활성화(DELETE) 후 즉시 410 차단 → 공유 있는 파일 영구 삭제 성공(FK 이슈 해결 + shares 행 제거).

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET. `python -m tests.integration_shares`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select, text

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import Share
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
PAYLOAD = b"MiniDrive-share-payload-0123456789" * 512  # ~17KB


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


async def _active_alice_headers(c: httpx.AsyncClient) -> dict[str, str]:
    _admin_h, code = await setup_admin(c)
    alice_h, _alice_id = await register_active(c, code, ALICE)
    return alice_h


async def _upload(c: httpx.AsyncClient, headers: dict[str, str], name: str) -> int:
    r = await c.post(
        "/api/files/upload",
        headers=headers,
        files={"file": (name, PAYLOAD, "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _direct_minio_bytes(accel: str) -> bytes:
    secure = "https" if settings.minio_secure else "http"
    minio_path = accel[len("/_minio") :]
    minio_url = f"{secure}://{settings.minio_endpoint}{minio_path}"
    async with httpx.AsyncClient(timeout=60) as raw:
        got = await raw.get(minio_url)
    assert got.status_code == 200, f"{got.status_code} {got.text[:200]}"
    return got.content


async def _set_expired(share_url: str) -> None:
    async with SessionFactory() as s:
        await s.execute(
            text("UPDATE shares SET expires_at = :ts WHERE share_url = :u"),
            {"ts": datetime.now(UTC) - timedelta(hours=1), "u": share_url},
        )
        await s.commit()


async def scenario() -> None:  # noqa: C901 - 통합 시나리오 한 흐름
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        alice_h = await _active_alice_headers(c)
        _ok("셋업 + 코드 가입 → alice 로그인")

        # --- 기본 공유: 생성 → 메타 → 다운로드(바이트 일치) --------------------
        file_id = await _upload(c, alice_h, "hello.bin")
        r = await c.post(
            "/api/shares", headers=alice_h, json={"file_id": file_id, "permission": "download"}
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["is_active"] is True and body["password_required"] is False
        assert body["download_count"] == 0 and body["file_name"] == "hello.bin"
        share_url = body["share_url"]
        _ok(f"공유 생성 (share_url={share_url[:10]}...)")

        # 무인증 메타 (Authorization 헤더 없이)
        r = await c.get(f"/api/public/shares/{share_url}")
        assert r.status_code == 200, r.text
        assert r.json()["file_name"] == "hello.bin"
        assert r.json()["size"] == len(PAYLOAD)
        assert r.json()["password_required"] is False
        _ok("무인증 메타 조회 (파일명/크기/비밀번호 필요 여부)")

        # 무인증 다운로드 → X-Accel-Redirect + 바이트 일치
        r = await c.post(f"/api/public/shares/{share_url}/download")
        assert r.status_code == 200, r.text
        accel = r.headers.get("X-Accel-Redirect")
        assert accel and accel.startswith("/_minio/"), accel
        assert r.headers.get("X-Share-Permission") == "download"
        assert await _direct_minio_bytes(accel) == PAYLOAD
        _ok("무인증 다운로드 (X-Accel-Redirect + presigned 직접 GET 바이트 일치)")

        # download_count 증가 반영
        r = await c.get("/api/shares", headers=alice_h)
        assert r.status_code == 200, r.text
        mine = {s["share_url"]: s for s in r.json()}
        assert mine[share_url]["download_count"] == 1, r.text
        _ok("목록에서 download_count=1 확인")

        # --- 공개 다운로드 티켓: 발급(횟수 소모) → 무헤더 GET → 재사용 실패 ------
        r = await c.post(f"/api/public/shares/{share_url}/download-ticket")
        assert r.status_code == 200, r.text
        ticket_url = r.json()["url"]
        assert r.json()["expires_in"] == 60
        # 티켓 발급이 download_count 를 소모(2)
        r2 = await c.get("/api/shares", headers=alice_h)
        assert {s["share_url"]: s for s in r2.json()}[share_url]["download_count"] == 2
        # 무헤더 GET → 바이트 일치
        r = await c.get(ticket_url)
        assert r.status_code == 200, r.text
        assert await _direct_minio_bytes(r.headers["X-Accel-Redirect"]) == PAYLOAD
        # 재사용 실패(1회용) + 무효 티켓 404
        assert (await c.get(ticket_url)).status_code == 404
        assert (
            await c.get("/api/public/shares/download", params={"ticket": "bogus"})
        ).status_code == 404
        _ok("공개 다운로드 티켓 (발급→GET 바이트 일치→재사용 404, 횟수 소모)")

        # --- 비밀번호 공유: 오답 401 / 정답 200 ------------------------------
        pw_file = await _upload(c, alice_h, "secret.bin")
        r = await c.post(
            "/api/shares",
            headers=alice_h,
            json={"file_id": pw_file, "permission": "read", "password": "s3cr3t!"},
        )
        assert r.status_code == 201 and r.json()["password_required"] is True, r.text
        pw_url = r.json()["share_url"]

        r = await c.get(f"/api/public/shares/{pw_url}")
        assert r.status_code == 200 and r.json()["password_required"] is True, r.text

        r = await c.post(f"/api/public/shares/{pw_url}/download", json={"password": "wrong"})
        assert r.status_code == 401, r.text
        r = await c.post(f"/api/public/shares/{pw_url}/download", json={})
        assert r.status_code == 401, r.text  # 비밀번호 누락도 401
        _ok("비밀번호 공유 오답/누락 401")

        r = await c.post(f"/api/public/shares/{pw_url}/download", json={"password": "s3cr3t!"})
        assert r.status_code == 200, r.text
        assert await _direct_minio_bytes(r.headers["X-Accel-Redirect"]) == PAYLOAD
        _ok("비밀번호 공유 정답 200 (바이트 일치)")

        # --- 만료 공유: 410 (메타 + 다운로드) --------------------------------
        exp_file = await _upload(c, alice_h, "expiring.bin")
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        r = await c.post(
            "/api/shares",
            headers=alice_h,
            json={"file_id": exp_file, "expires_at": future},
        )
        assert r.status_code == 201, r.text
        exp_url = r.json()["share_url"]
        await _set_expired(exp_url)  # DB 에서 과거로 만료 처리
        r = await c.get(f"/api/public/shares/{exp_url}")
        assert r.status_code == 410, r.text
        r = await c.post(f"/api/public/shares/{exp_url}/download")
        assert r.status_code == 410, r.text
        # 과거 만료 시각으로는 생성 거부(422)
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        r = await c.post(
            "/api/shares", headers=alice_h, json={"file_id": exp_file, "expires_at": past}
        )
        assert r.status_code == 422, r.text
        _ok("만료 공유 410 (메타/다운로드) + 과거 만료 생성 422")

        # --- max_downloads 소진: 첫 200, 두 번째 410 -------------------------
        cap_file = await _upload(c, alice_h, "capped.bin")
        r = await c.post(
            "/api/shares",
            headers=alice_h,
            json={"file_id": cap_file, "permission": "download", "max_downloads": 1},
        )
        assert r.status_code == 201, r.text
        cap_url = r.json()["share_url"]
        r = await c.post(f"/api/public/shares/{cap_url}/download")
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/public/shares/{cap_url}/download")
        assert r.status_code == 410, r.text
        _ok("max_downloads 소진 후 410")

        # --- 폴더 공유 400 / 없는 URL 404 ------------------------------------
        r = await c.post("/api/files", headers=alice_h, json={"name": "folder-x"})
        folder_id = r.json()["id"]
        r = await c.post("/api/shares", headers=alice_h, json={"file_id": folder_id})
        assert r.status_code == 400, r.text
        r = await c.get("/api/public/shares/does-not-exist")
        assert r.status_code == 404, r.text
        _ok("폴더 공유 400 / 없는 URL 404")

        # --- 비활성화(DELETE) 후 즉시 410 차단 -------------------------------
        dis_file = await _upload(c, alice_h, "disable-me.bin")
        r = await c.post("/api/shares", headers=alice_h, json={"file_id": dis_file})
        share_id = r.json()["id"]
        dis_url = r.json()["share_url"]
        # 비활성화 전에는 접근 가능
        assert (await c.get(f"/api/public/shares/{dis_url}")).status_code == 200
        r = await c.delete(f"/api/shares/{share_id}", headers=alice_h)
        assert r.status_code == 204, r.text
        # 비활성화 직후 즉시 410 (게이트웨이 모델)
        assert (await c.get(f"/api/public/shares/{dis_url}")).status_code == 410
        r = await c.post(f"/api/public/shares/{dis_url}/download")
        assert r.status_code == 410, r.text
        # 행은 남아 있어야 한다(이력 보존) — 목록에 여전히 존재(is_active=False)
        r = await c.get("/api/shares", headers=alice_h)
        disabled = next(s for s in r.json() if s["share_url"] == dis_url)
        assert disabled["is_active"] is False
        _ok("비활성화 후 즉시 410 + 행 보존(is_active=False)")

        # --- 공유 있는 파일 영구 삭제 성공 (FK 이슈 해결 확인) ----------------
        del_file = await _upload(c, alice_h, "to-delete.bin")
        r = await c.post("/api/shares", headers=alice_h, json={"file_id": del_file})
        del_share_url = r.json()["share_url"]
        await c.post(f"/api/files/{del_file}/delete", headers=alice_h)  # 소프트 삭제
        r = await c.post(f"/api/files/{del_file}/permanent-delete", headers=alice_h)
        assert r.status_code == 204, r.text  # FK 위반 없이 성공
        # 공유 행이 함께 제거됨 → 공개 접근 404
        assert (await c.get(f"/api/public/shares/{del_share_url}")).status_code == 404
        async with SessionFactory() as s:
            cnt = (
                await s.execute(
                    select(func.count()).select_from(Share).where(Share.file_id == del_file)
                )
            ).scalar_one()
            assert cnt == 0, f"shares 행이 {cnt}개 남음"
        _ok("공유 있는 파일 영구 삭제 성공 (FK 해결 + shares 행 제거)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n공유 링크 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
