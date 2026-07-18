"""그룹 CRUD + 멤버 관리 통합 검증 (실제 postgres+redis 필요, pytest 미수집).

시나리오 (PRD 3.1.1, 3.1.2, 6.4):
  그룹 생성 → 이름 중복 409 → 목록/상세 → 멤버 초대(member/admin)
  → 비권한자(member) 초대 403 → admin 초대 허용 → owner 직접 부여 400
  → 역할 변경(owner→admin 승격) → 비-owner 역할 변경 403
  → admin 이 admin 제거 403 → member 제거 204 → 재초대 upsert(행 1개 재활성화)
  → 소유권 이전(역할 스왑) → owner 자신 제거 400
  → 비-owner 삭제 403 → owner 삭제 204 (권한 행 제거 + 파일 group_id NULL)
  → 삭제 그룹 상세 404 / 목록 제외 → audit_logs 기록 확인.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET. `python -m tests.integration_groups`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import AuditLog, File, FileGroupPermission, GroupMember, User
from app.services.users import create_root_folder
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

USERS = {
    "alice": {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"},
    "bob": {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"},
    "carol": {"email": "carol@example.com", "password": "Passw0rd!", "display_name": "Carol"},
    "dave": {"email": "dave@example.com", "password": "Passw0rd!", "display_name": "Dave"},
}


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()


async def _member_row_count(group_id: int, user_id: int) -> int:
    """group_members 테이블의 (group_id, user_id) 물리 행 수 (removed 포함)."""
    async with SessionFactory() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(GroupMember)
                .where(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == user_id,
                )
            )
        ).scalar_one()


async def _audit_actions() -> list[str]:
    async with SessionFactory() as s:
        rows = (
            await s.execute(
                select(AuditLog.action).where(AuditLog.target_type == "group")
            )
        ).scalars().all()
    return list(rows)


def _role_of(members: list[dict], uid: int) -> str | None:
    return next((m["role"] for m in members if m["user_id"] == uid), None)


async def scenario() -> None:  # noqa: C901 - 순차 시나리오
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60
    ) as c:
        admin_h, code = await setup_admin(c)
        alice_h, alice_id = await register_active(c, code, USERS["alice"])
        bob_h, bob_id = await register_active(c, code, USERS["bob"])
        carol_h, carol_id = await register_active(c, code, USERS["carol"])
        dave_h, dave_id = await register_active(c, code, USERS["dave"])
        _ok("셋업 + 코드로 4명 즉시 active 가입")

        # 1. 그룹 생성 — 생성자가 owner 자동 등록
        r = await c.post(
            "/api/groups",
            headers=alice_h,
            json={"name": "개발1팀", "description": "백엔드"},
        )
        assert r.status_code == 201, r.text
        gid = r.json()["id"]
        assert r.json()["owner_user_id"] == alice_id
        _ok("그룹 생성 (alice=owner)")

        # 2. 이름 중복 → 409
        r = await c.post("/api/groups", headers=bob_h, json={"name": "개발1팀"})
        assert r.status_code == 409, r.text
        _ok("이름 중복 409")

        # 3. 목록 — 멤버 수 1, alice 역할 owner
        r = await c.get("/api/groups", headers=alice_h)
        assert r.status_code == 200, r.text
        item = next(g for g in r.json()["items"] if g["id"] == gid)
        assert item["member_count"] == 1 and item["my_role"] == "owner", item
        # 비멤버(bob) 시점엔 my_role None
        r = await c.get("/api/groups", headers=bob_h)
        item = next(g for g in r.json()["items"] if g["id"] == gid)
        assert item["my_role"] is None, item
        _ok("목록 (멤버 수 / 내 역할 / 비멤버 None)")

        # 4. 상세 — 비멤버(bob)도 조회 가능
        r = await c.get(f"/api/groups/{gid}", headers=bob_h)
        assert r.status_code == 200, r.text
        assert len(r.json()["members"]) == 1
        assert _role_of(r.json()["members"], alice_id) == "owner"
        _ok("상세 조회 (비멤버 허용)")

        # 5. 멤버 초대 — bob(member), carol(admin)
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201 and r.json()["role"] == "member", r.text
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": carol_id, "role": "admin"},
        )
        assert r.status_code == 201 and r.json()["role"] == "admin", r.text
        _ok("초대 (bob=member, carol=admin)")

        # 6. 비권한자(member=bob) 초대 → 403
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=bob_h,
            json={"user_id": dave_id, "role": "member"},
        )
        assert r.status_code == 403, r.text
        _ok("member 초대 시도 403")

        # 7. admin(carol) 은 초대 가능 → dave 초대
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=carol_h,
            json={"user_id": dave_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        _ok("admin 초대 허용 (dave=member)")

        # 8. owner 역할 직접 부여 → 400
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": dave_id, "role": "owner"},
        )
        assert r.status_code == 400, r.text
        _ok("owner 직접 부여 400")

        # 9. 비활성 사용자 초대 불가 — 코드 가입 후 admin 이 inactive 로 전환
        r = await c.post("/api/auth/register", json={
            "email": "inactive@example.com", "password": "Passw0rd!",
            "display_name": "Inact", "signup_code": code,
        })
        assert r.status_code == 201, r.text
        inactive_id = r.json()["id"]
        r = await c.patch(
            f"/api/admin/users/{inactive_id}",
            headers=admin_h,
            json={"status": "inactive"},
        )
        assert r.status_code == 200, r.text
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": inactive_id, "role": "member"},
        )
        assert r.status_code == 400, r.text
        _ok("비활성 사용자 초대 400")

        # 10. 역할 변경 — owner 가 bob(member) → admin 승격
        r = await c.put(
            f"/api/groups/{gid}/members/{bob_id}/role",
            headers=alice_h,
            json={"role": "admin"},
        )
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text
        _ok("역할 변경 (bob → admin)")

        # 11. 비-owner(carol=admin) 역할 변경 → 403
        r = await c.put(
            f"/api/groups/{gid}/members/{dave_id}/role",
            headers=carol_h,
            json={"role": "admin"},
        )
        assert r.status_code == 403, r.text
        _ok("비-owner 역할 변경 403")

        # 12. admin(carol) 이 admin(bob) 제거 → 403
        r = await c.request(
            "DELETE", f"/api/groups/{gid}/members/{bob_id}", headers=carol_h
        )
        assert r.status_code == 403, r.text
        _ok("admin 이 admin 제거 403")

        # 13. member(dave) 제거 → 204 (owner 수행)
        r = await c.request(
            "DELETE", f"/api/groups/{gid}/members/{dave_id}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        assert await _member_row_count(gid, dave_id) == 1  # soft delete: 행 유지
        _ok("member 제거 204 (soft delete)")

        # 14. 재초대 upsert — dave 재초대 시 새 행 INSERT 아님(행 1개 재활성화)
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": dave_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        assert await _member_row_count(gid, dave_id) == 1, "재초대가 새 행을 만들면 안 됨"
        r = await c.get(f"/api/groups/{gid}/members", headers=alice_h)
        active = [m["user_id"] for m in r.json()["items"]]
        assert dave_id in active and active.count(dave_id) == 1
        _ok("재초대 upsert (행 1개 재활성화)")

        # 15. 소유권 이전 — alice(owner) → bob. 역할 스왑
        r = await c.post(
            f"/api/groups/{gid}/transfer-ownership",
            headers=alice_h,
            json={"user_id": bob_id},
        )
        assert r.status_code == 200 and r.json()["owner_user_id"] == bob_id, r.text
        r = await c.get(f"/api/groups/{gid}", headers=bob_h)
        assert _role_of(r.json()["members"], bob_id) == "owner"
        assert _role_of(r.json()["members"], alice_id) == "admin"
        _ok("소유권 이전 (bob=owner, alice=admin)")

        # 16. owner(bob) 자신 제거 → 400
        r = await c.request(
            "DELETE", f"/api/groups/{gid}/members/{bob_id}", headers=bob_h
        )
        assert r.status_code == 400, r.text
        _ok("owner 자신 제거 400")

        # 17. 파일 권한/그룹 소유 파일을 직접 심어 삭제 부수효과 검증 준비
        async with SessionFactory() as s:
            alice = (await s.execute(select(User).where(User.id == alice_id))).scalar_one()
            root = await create_root_folder(s, alice)
            f = File(
                user_id=alice_id,
                group_id=gid,  # 그룹 소유 파일
                parent_folder_id=root.id,
                name="group-doc.bin",
                file_key=f"users/{alice_id}/doc",
                mime_type="application/octet-stream",
                size=10,
            )
            s.add(f)
            await s.flush()
            s.add(
                FileGroupPermission(
                    file_id=f.id, group_id=gid, permission="read", granted_by=alice_id
                )
            )
            await s.commit()
            file_id = f.id
        _ok("그룹 소유 파일 + 권한 행 삽입")

        # 18. 비-owner(alice=admin) 삭제 → 403
        r = await c.request("DELETE", f"/api/groups/{gid}", headers=alice_h)
        assert r.status_code == 403, r.text
        _ok("비-owner 그룹 삭제 403")

        # 19. owner(bob) 삭제 → 204 + 권한 행 제거 + 파일 group_id NULL
        r = await c.request("DELETE", f"/api/groups/{gid}", headers=bob_h)
        assert r.status_code == 204, r.text
        async with SessionFactory() as s:
            perm_count = (
                await s.execute(
                    select(func.count())
                    .select_from(FileGroupPermission)
                    .where(FileGroupPermission.group_id == gid)
                )
            ).scalar_one()
            file_group = (
                await s.execute(select(File.group_id).where(File.id == file_id))
            ).scalar_one()
        assert perm_count == 0, "삭제 시 file_group_permissions 행이 남음"
        assert file_group is None, "그룹 소유 파일이 개인 소유로 전환되지 않음"
        _ok("owner 삭제 204 (권한 행 제거 + 파일 group_id NULL)")

        # 20. 삭제 그룹 — 상세 404, 목록 제외
        r = await c.get(f"/api/groups/{gid}", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.get("/api/groups", headers=bob_h)
        assert all(g["id"] != gid for g in r.json()["items"])
        _ok("삭제 후 상세 404 / 목록 제외")

        # 21. audit_logs — 기대 액션 모두 기록
        actions = await _audit_actions()
        for expected in (
            "group.create",
            "group.member_add",
            "group.member_role",
            "group.member_remove",
            "group.transfer_ownership",
            "group.delete",
        ):
            assert expected in actions, f"audit 누락: {expected} in {actions}"
        _ok(f"audit_logs 기록 확인 ({len(actions)} rows)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n그룹 CRUD + 멤버 관리 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
