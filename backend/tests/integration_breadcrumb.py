"""폴더 breadcrumb API 통합 검증 — GET /api/files/{id}/breadcrumb (실 postgres+redis+minio).

폴더 URL(/f/:id)로 새로고침·링크 진입했을 때 화면이 상위로 올라갈 길을 세울 수 있어야 한다.
핵심은 **열 수 있는 조상만 담는다**는 것이다 — 남의 드라이브 폴더를 crumb 으로 보여주면
눌러도 404 라서, 뒤로 갈 곳이 있는 것처럼 보이기만 하고 실제로는 막다른 길이 된다.

시나리오:
  A/B 가입
  [소유]     A: 루트 > 기획 > 2026 > 계획  → A 가 조회하면 crumbs 3칸, shared=false
  [루트직속] A: 루트 > 기획              → crumbs 1칸
  [파일]     A: 계획 안의 파일           → 파일도 자기 자신까지 (부모 체인 + 파일)
  [차단]     B 가 A 의 폴더 조회         → 404 (존재 자체를 숨긴다)
  [공유절단] A 가 "2026" 을 그룹 G(B 포함)에 read 상속 부여
             → B 가 "계획" 조회 시 crumbs 는 2026 부터 2칸, shared=true
               (그 위 "기획" 은 B 가 못 여는 남의 폴더라 잘려 있어야 한다)
  [공유루트] B 가 "2026" 자체 조회       → crumbs 1칸, shared=true
  [조상소유] B 가 A 의 폴더 안에 만든 항목을 A 가 조회 → shared=false (내 드라이브 경로)

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.
실행: `python -m tests.integration_breadcrumb`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, engine
from app.core.redis import redis_client
from app.main import app
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

USERS = {
    "alice": {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"},
    "bob": {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"},
}


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


async def _mkdir(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent_id: int | None = None
) -> int:
    r = await c.post("/api/files", headers=h, json={"name": name, "parent_id": parent_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _crumbs(
    c: httpx.AsyncClient, h: dict[str, str], file_id: int
) -> tuple[list[str], bool]:
    """(crumb 이름들, shared) 로 줄여 읽는다 — id 는 순서 검증에만 쓰이므로 이름으로 본다."""
    r = await c.get(f"/api/files/{file_id}/breadcrumb", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    return [x["name"] for x in body["crumbs"]], body["shared"]


async def scenario() -> None:
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60
    ) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, _alice_id = await register_active(c, code, USERS["alice"])
        bob_h, bob_id = await register_active(c, code, USERS["bob"])
        _ok("셋업 + 코드 가입 → alice(A)/bob(B) 로그인")

        # === A 의 폴더 트리: 기획 > 2026 > 계획 ==============================
        plan = await _mkdir(c, alice_h, "기획")
        y2026 = await _mkdir(c, alice_h, "2026", plan)
        detail = await _mkdir(c, alice_h, "계획", y2026)

        names, shared = await _crumbs(c, alice_h, detail)
        assert names == ["기획", "2026", "계획"], names
        assert shared is False
        _ok("소유 폴더: 루트 아래부터 자기 자신까지 3칸 (shared=false)")

        names, shared = await _crumbs(c, alice_h, plan)
        assert names == ["기획"] and shared is False, (names, shared)
        _ok("루트 직속 폴더: 1칸 (루트 칸은 서버가 넣지 않는다)")

        # 파일도 같은 규칙 — 부모 체인 + 자기 자신.
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("memo.txt", b"hello", "text/plain")},
            data={"parent_id": str(detail)},
        )
        assert r.status_code == 201, r.text
        names, _ = await _crumbs(c, alice_h, r.json()["id"])
        assert names == ["기획", "2026", "계획", "memo.txt"], names
        _ok("파일: 부모 체인 + 자기 자신")

        # === 권한 없는 사용자 =================================================
        r = await c.get(f"/api/files/{detail}/breadcrumb", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("권한 없는 폴더: 404 (존재 여부를 숨긴다)")

        # === 공유 — 부여 지점 위쪽은 잘라낸다 =================================
        r = await c.post("/api/groups", headers=alice_h, json={"name": "G팀"})
        assert r.status_code == 201, r.text
        gid = r.json()["id"]
        r = await c.post(
            f"/api/groups/{gid}/members", headers=alice_h, json={"user_id": bob_id}
        )
        assert r.status_code in (200, 201, 204), r.text
        r = await c.post(
            f"/api/files/{y2026}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text

        names, shared = await _crumbs(c, bob_h, detail)
        assert names == ["2026", "계획"], names
        assert shared is True
        _ok('공유받은 하위 폴더: 부여 지점("2026")부터 2칸, shared=true — "기획"은 잘림')

        names, shared = await _crumbs(c, bob_h, y2026)
        assert names == ["2026"] and shared is True, (names, shared)
        _ok("공유 루트 자체: 1칸, shared=true")

        # A 는 여전히 자기 경로 전체를 본다 (공유가 소유자 시야를 바꾸지 않는다).
        names, shared = await _crumbs(c, alice_h, detail)
        assert names == ["기획", "2026", "계획"] and shared is False, (names, shared)
        _ok("공유 후에도 소유자 시야는 그대로 (shared=false)")

        # === 조상 소유 — 내 폴더 안에 협업자가 만든 항목 ======================
        # B 에게 write 로 올려 "계획" 안에 폴더를 만들게 한다.
        r = await c.put(
            f"/api/files/{y2026}/permissions/{gid}",
            headers=alice_h,
            json={"permission": "write", "inherit_to_children": True},
        )
        assert r.status_code == 200, r.text
        bob_folder = await _mkdir(c, bob_h, "B의작업", detail)

        # A 는 소유자가 아니지만(파일 user_id=B) 조상 폴더 소유자다 — 내 드라이브 경로다.
        names, shared = await _crumbs(c, alice_h, bob_folder)
        assert names == ["기획", "2026", "계획", "B의작업"], names
        assert shared is False, "조상 소유 경로는 공유가 아니다"
        _ok("조상 폴더 소유자: 체인을 자르지 않고 shared=false")

        # B 시점에서는 여전히 공유 경로다.
        names, shared = await _crumbs(c, bob_h, bob_folder)
        assert names == ["2026", "계획", "B의작업"] and shared is True, (names, shared)
        _ok("같은 폴더라도 B 시점에서는 공유 경로 (shared=true)")

    await engine.dispose()
    await redis_client.aclose()
    print("\nbreadcrumb 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
