"""파일 그룹 권한 + 상속 판정 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오 (PRD 3.1.3, 5.7, 6.5/6.6):
  A 가 root/depth1/depth2/file + depth1/depth2b 트리 구성 → 그룹 G 생성, B 를 member 로
  → 부여 전 B 접근 404/none
  → depth1 에 G read 부여 → B 가 depth2 하위 file 다운로드 성공(상속) + depth2b 상속 접근
  → B 쓰기 시도 차단(403/404) + manage 시도 403 + 공유 링크 생성 차단
  → depth2 에 G write 부여(재정의) → B 업로드(새 버전) 성공
  → depth1 권한 inherit_to_children=FALSE → depth2b 상속 소실(404), depth1·file 유지(재정의)
  → 회수 직전 접근으로 캐시 적재 후 depth2 권한 회수 → B 접근 즉시 차단(캐시 무효화)
  → 만료 권한 무효(과거 expires_at) → 미래로 갱신 시 접근 회복
  → B 가 shared-with-me 에서 부여 지점 확인 → permissions/check 응답 확인.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.
실행: `python -m tests.integration_permissions`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, engine
from app.core.redis import redis_client
from app.main import app
from app.services.storage import storage_service
from app.services.users import ensure_admin_bootstrap

USERS = {
    "alice": {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"},
    "bob": {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"},
}
PAYLOAD = b"MiniDrive-permission-inheritance-test-payload"


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


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


async def _admin_headers(c: httpx.AsyncClient) -> dict[str, str]:
    r = await c.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_initial_password},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _register_approve(
    c: httpx.AsyncClient, admin_h: dict[str, str], creds: dict[str, str]
) -> tuple[dict[str, str], int]:
    r = await c.post("/api/auth/register", json=creds)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = await c.post(f"/api/admin/users/{uid}/approve", headers=admin_h)
    assert r.status_code == 200, r.text
    r = await c.post(
        "/api/auth/login", json={"email": creds["email"], "password": creds["password"]}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, uid


async def _mkfolder(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent_id: int | None
) -> int:
    r = await c.post("/api/files", headers=h, json={"name": name, "parent_id": parent_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent_id: int
) -> int:
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, PAYLOAD, "application/octet-stream")},
        data={"parent_id": str(parent_id)},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def scenario() -> None:  # noqa: C901, PLR0915 - 순차 시나리오
    await _reset()

    from app.core.database import SessionFactory

    async with SessionFactory() as session:
        admin = await ensure_admin_bootstrap(
            session, settings.admin_email, settings.admin_initial_password
        )
    assert admin is not None
    _ok("admin 부트스트랩")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60
    ) as c:
        admin_h = await _admin_headers(c)
        alice_h, alice_id = await _register_approve(c, admin_h, USERS["alice"])
        bob_h, bob_id = await _register_approve(c, admin_h, USERS["bob"])
        _ok("alice(A)/bob(B) 승인·로그인")

        # 1. A 가 트리 구성: root/depth1/{depth2/file, depth2b}
        depth1 = await _mkfolder(c, alice_h, "depth1", None)
        depth2 = await _mkfolder(c, alice_h, "depth2", depth1)
        depth2b = await _mkfolder(c, alice_h, "depth2b", depth1)
        file_id = await _upload(c, alice_h, "file.bin", depth2)
        _ok("A 트리 구성 (depth1/depth2/file, depth1/depth2b)")

        # 2. 그룹 G 생성 + B member 초대
        r = await c.post("/api/groups", headers=alice_h, json={"name": "G팀"})
        assert r.status_code == 201, r.text
        gid = r.json()["id"]
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        _ok("그룹 G 생성 + B member")

        # 3. 부여 전 — B 는 파일 접근 불가 (404) / check none
        r = await c.get(f"/api/files/{file_id}", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.get(f"/api/permissions/check/{file_id}", headers=bob_h)
        assert r.status_code == 200 and r.json()["permission"] == "none", r.text
        _ok("부여 전 B 접근 404 / check none")

        # 4. depth1 에 G read 부여 (상속 on)
        r = await c.post(
            f"/api/files/{depth1}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        _ok("depth1 에 G read 부여")

        # 5. B 가 2단계 하위 file 을 상속으로 접근/다운로드 (핵심: 상속 판정)
        r = await c.get(f"/api/files/{file_id}", headers=bob_h)
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/files/{file_id}/download", headers=bob_h)
        assert r.status_code == 200 and "X-Accel-Redirect" in r.headers, r.text
        _ok("B 상속 다운로드 성공 (depth1 read → file)")

        # depth2b(형제 폴더)도 상속 접근, depth2 목록에 file 노출
        r = await c.get(f"/api/files/{depth2b}", headers=bob_h)
        assert r.status_code == 200, r.text
        r = await c.get("/api/files", headers=bob_h, params={"parentId": depth2})
        assert r.status_code == 200, r.text
        assert any(it["id"] == file_id for it in r.json()["items"]), r.text
        _ok("B 상속으로 depth2b 접근 + 남의 폴더 목록 조회")

        # 6. B 쓰기 시도 차단 (read 만 보유) — reupload/rename 403|404
        r = await c.post(
            f"/api/files/{file_id}/upload",
            headers=bob_h,
            files={"file": ("file.bin", b"nope", "application/octet-stream")},
        )
        assert r.status_code in (403, 404), r.text
        r = await c.put(f"/api/files/{file_id}", headers=bob_h, json={"name": "hacked.bin"})
        assert r.status_code in (403, 404), r.text
        _ok("B 쓰기 시도 차단 (403/404)")

        # 7. B 는 권한 관리 불가(manage 아님) + 공유 링크 생성 불가(소유자 전용)
        r = await c.post(
            f"/api/files/{depth2}/permissions",
            headers=bob_h,
            json={"group_id": gid, "permission": "read"},
        )
        assert r.status_code == 403, r.text
        r = await c.post("/api/shares", headers=bob_h, json={"file_id": file_id})
        assert r.status_code in (403, 404), r.text
        _ok("B manage 시도 403 + 공유 링크 생성 차단")

        # 8. depth2 에 G write 부여 (재정의 — 더 가까운 조상)
        r = await c.post(
            f"/api/files/{depth2}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "write", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        # B 업로드(새 버전) 성공 + check = write / via group / source depth2
        r = await c.post(
            f"/api/files/{file_id}/upload",
            headers=bob_h,
            files={"file": ("file.bin", PAYLOAD + b"-v2", "application/octet-stream")},
        )
        assert r.status_code == 201, r.text
        r = await c.get(f"/api/permissions/check/{file_id}", headers=bob_h)
        body = r.json()
        assert body["permission"] == "write" and body["via"] == "group", body
        assert body["source_file_id"] == depth2, body
        _ok("depth2 write 재정의 → B 업로드 성공 (check write/group/source=depth2)")

        # 9. depth1 권한 inherit_to_children=FALSE → 상속 차단, 자신은 유지
        r = await c.put(
            f"/api/files/{depth1}/permissions/{gid}",
            headers=alice_h,
            json={"inherit_to_children": False},
        )
        assert r.status_code == 200, r.text
        # depth2b(상속만 의존) 접근 소실
        r = await c.get(f"/api/files/{depth2b}", headers=bob_h)
        assert r.status_code == 404, r.text
        # depth1 자신은 depth0 이므로 inherit 무관하게 read 유지
        r = await c.get(f"/api/files/{depth1}", headers=bob_h)
        assert r.status_code == 200, r.text
        # file 은 depth2 재정의로 여전히 접근
        r = await c.get(f"/api/files/{file_id}", headers=bob_h)
        assert r.status_code == 200, r.text
        _ok("depth1 inherit=FALSE → depth2b 소실 / depth1·file 유지 (재정의 판정)")

        # 10. 캐시 무효화: 회수 직전 접근으로 캐시 적재 후 depth2 권한 회수 → 즉시 차단
        r = await c.get(f"/api/files/{file_id}/download", headers=bob_h)
        assert r.status_code == 200, r.text  # 캐시 적재
        r = await c.request(
            "DELETE", f"/api/files/{depth2}/permissions/{gid}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        r = await c.get(f"/api/files/{file_id}/download", headers=bob_h)
        assert r.status_code == 404, r.text  # 캐시 무효화되어 즉시 차단
        _ok("권한 회수 후 즉시 차단 (캐시 무효화 검증)")

        # 11. 만료 권한 무효: file 에 과거 만료 read 부여 → 여전히 차단
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        r = await c.post(
            f"/api/files/{file_id}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "read", "expires_at": past},
        )
        assert r.status_code == 201, r.text
        r = await c.get(f"/api/files/{file_id}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("만료(과거) 권한 무효 — 접근 차단 유지")

        # 미래 만료로 갱신 → 접근 회복
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        r = await c.put(
            f"/api/files/{file_id}/permissions/{gid}",
            headers=alice_h,
            json={"expires_at": future},
        )
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/files/{file_id}", headers=bob_h)
        assert r.status_code == 200, r.text
        _ok("만료 미래 갱신 → 접근 회복")

        # 12. shared-with-me — B 는 부여 지점(depth1, file)을 그룹명/권한과 함께 확인
        r = await c.get("/api/files/shared-with-me", headers=bob_h)
        assert r.status_code == 200, r.text
        items = {it["file"]["id"]: it for it in r.json()["items"]}
        assert depth1 in items and items[depth1]["group_name"] == "G팀", items
        assert items[depth1]["permission"] == "read", items
        assert file_id in items, items
        _ok("shared-with-me — 부여 지점(depth1/file) 확인")

        # 13. permissions/check — 소유자 A 는 manage/owner
        r = await c.get(f"/api/permissions/check/{file_id}", headers=alice_h)
        body = r.json()
        assert body["permission"] == "manage" and body["via"] == "owner", body
        # B 는 file 직접 read (미래 만료) / via group / source=file
        r = await c.get(f"/api/permissions/check/{file_id}", headers=bob_h)
        body = r.json()
        assert body["permission"] == "read" and body["via"] == "group", body
        assert body["source_file_id"] == file_id, body
        _ok("permissions/check — A manage/owner, B read/group/source=file")

        # 14. 권한 목록 — depth1 GET /permissions 로 직접+상속 확인 (A=owner)
        r = await c.get(f"/api/files/{depth2b}/permissions", headers=alice_h)
        assert r.status_code == 200, r.text
        # depth2b 는 직접 부여 없음, depth1 read 는 inherit=FALSE 라 상속 목록에서도 제외
        assert r.json()["direct"] == [], r.json()
        assert r.json()["inherited"] == [], r.json()
        _ok("GET /permissions — depth2b 직접/상속 목록 (inherit=FALSE 반영)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n파일 그룹 권한 + 상속 판정 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
