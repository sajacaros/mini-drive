"""@전사 시스템 그룹 통합 검증 (spec/wiki-index.md).

위키의 "전사 공개"는 새 접근 경로가 아니라 `@전사` 그룹에 read 를 주는 것이다. 그래서 검증할
것은 위키 기능이 아니라 **이 그룹이 기존 권한 체계 안에서 정상 시민으로 동작하는가**다.

검증 축:
  1. 셋업 위저드가 @전사 를 만든다 (create_admin_account 훅).
  2. 그룹 목록에 보인다 — 권한 부여 대상으로 고를 수 있어야 하므로.
  3. 편집/삭제/멤버 조작은 400 으로 막힌다.
  4. @전사 에 read 를 주면 **멤버 등록 없이** 다른 사용자가 즉시 접근한다
     (get_user_group_ids 의 UNION 이 실제로 동작하는지).
  5. 회수하면 즉시 차단된다 (시스템 그룹 캐시 무효화가 전 활성 사용자를 덮는지).
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import Group
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}
PAYLOAD = b"MiniDrive-wiki-all-users-group-test"


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


async def main() -> None:
    await _reset()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        alice_h, code = await setup_admin(c)
        bob_h, bob_id = await register_active(c, code, BOB)

        # 1. 셋업이 시스템 그룹을 만들었는가 (create_all 로는 안 생기므로 훅이 유일한 경로)
        async with SessionFactory() as session:
            group = (
                await session.execute(select(Group).where(Group.is_system.is_(True)))
            ).scalars().one()
            sys_gid, sys_name = group.id, group.name
        _ok(f"셋업 위저드가 시스템 그룹 생성: {sys_name} (id={sys_gid})")

        # 2. 그룹 목록 노출 — 권한 부여 대상으로 선택 가능해야 한다
        r = await c.get("/api/groups?page=1&size=100", headers=alice_h)
        assert r.status_code == 200, r.text
        names = [g["name"] for g in r.json()["items"]]
        assert sys_name in names, names
        _ok("그룹 목록에 노출 — 권한 부여 대상으로 선택 가능")

        # 3. 편집/삭제/멤버 조작 차단
        r = await c.put(
            f"/api/groups/{sys_gid}", headers=alice_h, json={"name": "바꾸기시도"}
        )
        assert r.status_code == 400, r.text
        r = await c.request("DELETE", f"/api/groups/{sys_gid}", headers=alice_h)
        assert r.status_code == 400, r.text
        r = await c.post(
            f"/api/groups/{sys_gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 400, r.text
        _ok("이름 변경·삭제·멤버 추가 전부 400 차단")

        # 4. @전사 read 부여 → bob 이 멤버 등록 없이 즉시 접근
        r = await c.post("/api/files", headers=alice_h, json={"name": "위키폴더"})
        assert r.status_code == 201, r.text
        folder = r.json()["id"]
        r = await c.post(
            "/api/files/upload",
            headers=alice_h,
            files={"file": ("doc.md", PAYLOAD, "text/markdown")},
            data={"parent_id": str(folder)},
        )
        assert r.status_code == 201, r.text
        fid = r.json()["id"]

        r = await c.get(f"/api/files/{fid}", headers=bob_h)
        assert r.status_code == 404, r.text  # 부여 전에는 존재도 안 보인다
        _ok("부여 전 — bob 은 404")

        r = await c.post(
            f"/api/files/{folder}/permissions",
            headers=alice_h,
            json={
                "group_id": sys_gid,
                "permission": "read",
                "inherit_to_children": True,
            },
        )
        assert r.status_code == 201, r.text
        r = await c.get(f"/api/files/{fid}", headers=bob_h)
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/files/{fid}/download", headers=bob_h)
        assert r.status_code == 200, r.text
        _ok("@전사 read 부여 → 멤버 등록 없이 즉시 열람·다운로드 (UNION 동작)")

        # 5. 회수 → 즉시 차단. 캐시를 태운 뒤 회수해야 무효화 검증이 된다.
        r = await c.request(
            "DELETE", f"/api/files/{folder}/permissions/{sys_gid}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        r = await c.get(f"/api/files/{fid}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("회수 → 즉시 차단 (시스템 그룹 캐시 무효화가 전 사용자를 덮음)")

    await engine.dispose()
    print("\n@전사 시스템 그룹 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
