"""Admin API + 이메일 조회 + rate limiting 통합 검증 (실제 postgres+redis 필요, pytest 미수집).

MinIO 없이 동작하도록 파일/공유 행은 DB 로 직접 심는다(내용 다운로드는 검증 범위 밖 —
admin 은 내용 접근 불가, PRD 3.6.4). httpx(ASGITransport)로 검증한다.

시나리오:
  admin 생성(CLI 경로) → alice/bob active, carol inactive → 그룹/파일/공유 DB 심기 →
  admin stats(사전 데이터 반영) → groups/shares 목록 → 강제 비활성화 후 공개 메타 410 +
  audit(share.force_disable) 기록 → audit-logs 필터 → lookup(정확 일치 200/부분 404/비활성 404)
  → 로그인 6회째 429(Retry-After) → 다른 IP 독립 카운트 → admin 파일 내용 엔드포인트 부재.

env: DATABASE_URL / REDIS_URL. `python -m tests.integration_admin`.
"""

from __future__ import annotations

import asyncio
import secrets
import sys

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import AuditLog, File, Group, GroupMember, Share, User
from app.models.enums import GroupRole, SharePermission, UserRole, UserStatus
from app.services.setup import create_admin_account
from app.services.users import create_root_folder
from tests._bootstrap import ADMIN_EMAIL, ADMIN_PASSWORD
from tests._dbreset import stamp_alembic_head

ADMIN = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}
CAROL = {"email": "carol@example.com", "password": "Passw0rd!", "display_name": "Carol"}


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()


async def _seed() -> dict[str, int]:
    """DB 로 사용자/그룹/파일/공유를 직접 심는다. 반환: 주요 id 매핑."""
    async with SessionFactory() as s:
        admin = await create_admin_account(s, ADMIN_EMAIL, ADMIN_PASSWORD)
        await s.commit()
    assert admin is not None

    from app.core.security import hash_password

    async with SessionFactory() as s:
        alice = User(
            email=ALICE["email"], password_hash=hash_password(ALICE["password"]),
            display_name=ALICE["display_name"], role=UserRole.USER, status=UserStatus.ACTIVE,
            storage_used=3000,
        )
        bob = User(
            email=BOB["email"], password_hash=hash_password(BOB["password"]),
            display_name=BOB["display_name"], role=UserRole.USER, status=UserStatus.ACTIVE,
            storage_used=1000,
        )
        carol = User(
            email=CAROL["email"], password_hash=hash_password(CAROL["password"]),
            display_name=CAROL["display_name"], role=UserRole.USER, status=UserStatus.INACTIVE,
        )
        s.add_all([alice, bob, carol])
        await s.flush()
        alice_root = await create_root_folder(s, alice)
        await create_root_folder(s, bob)

        # 그룹 (alice owner, bob member).
        group = Group(name="개발1팀", description="dev", owner_user_id=alice.id, is_active=True)
        s.add(group)
        await s.flush()
        s.add_all([
            GroupMember(group_id=group.id, user_id=alice.id, role=GroupRole.OWNER),
            GroupMember(group_id=group.id, user_id=bob.id, role=GroupRole.MEMBER),
        ])

        # 파일 2개 (alice), 하나는 그룹 소유.
        f1 = File(
            user_id=alice.id, parent_folder_id=alice_root.id, name="report.pdf",
            file_key=f"users/{alice.id}/f1", mime_type="application/pdf", size=2000,
            is_folder=False,
        )
        f2 = File(
            user_id=alice.id, group_id=group.id, parent_folder_id=alice_root.id,
            name="team.xlsx", file_key=f"users/{alice.id}/f2", size=1000, is_folder=False,
        )
        s.add_all([f1, f2])
        await s.flush()

        # 공유 링크 2개 (f1: active, f2: inactive).
        share_active = Share(
            file_id=f1.id, created_by=alice.id, share_url=secrets.token_urlsafe(16),
            permission=SharePermission.READ, is_active=True,
        )
        share_inactive = Share(
            file_id=f2.id, created_by=alice.id, share_url=secrets.token_urlsafe(16),
            permission=SharePermission.DOWNLOAD, is_active=False,
        )
        s.add_all([share_active, share_inactive])
        await s.commit()

        return {
            "admin": admin.id, "alice": alice.id, "bob": bob.id, "carol": carol.id,
            "group": group.id, "f1": f1.id, "f2": f2.id,
            "share_active": share_active.id, "share_active_url": share_active.share_url,
            "share_inactive": share_inactive.id,
        }


async def _auth_headers(c: httpx.AsyncClient, creds: dict, real_ip: str) -> dict[str, str]:
    r = await c.post(
        "/api/auth/login",
        json={"email": creds["email"], "password": creds["password"]},
        headers={"X-Real-IP": real_ip},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def scenario() -> None:
    await _reset()
    ids = await _seed()
    _ok("시드 완료 (admin/alice/bob/carol + 그룹 + 파일2 + 공유2)")

    # rate limit 이 셋업 로그인을 방해하지 않도록 넉넉히 두고, 전용 테스트에서 5 로 조인다.
    settings.rate_limit_login_per_min = 1000

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        admin_h = await _auth_headers(c, ADMIN, "172.16.0.1")
        alice_h = await _auth_headers(c, ALICE, "172.16.0.2")

        # 1. admin stats — 사전 데이터 반영.
        r = await c.get("/api/admin/stats", headers=admin_h)
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["users_by_status"]["active"] == 3  # admin, alice, bob
        assert st["users_by_status"]["inactive"] == 1  # carol
        assert st["total_users"] == 4
        assert st["total_files"] == 2
        # 루트 폴더 3개(admin/alice/bob) 가 폴더로 집계된다.
        assert st["total_folders"] == 3
        assert st["total_shares"] == {"active": 1, "inactive": 1, "total": 2}
        assert st["total_groups"] == 1
        assert st["total_storage_used"] == 4000  # alice 3000 + bob 1000
        assert st["top_users"][0]["email"] == ALICE["email"]  # 사용량 최대
        _ok("GET /admin/stats — 상태별 사용자/파일/폴더/공유/그룹/사용량/top_users 반영")

        # 2. groups 목록.
        r = await c.get("/api/admin/groups", headers=admin_h)
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["total"] == 1
        item = g["items"][0]
        assert item["owner_email"] == ALICE["email"]
        assert item["member_count"] == 2  # alice + bob
        assert item["file_count"] == 1    # f2 만 group 소유
        _ok("GET /admin/groups — owner 이메일·멤버 수·소유 파일 수")

        # 3. shares 목록 + 필터.
        r = await c.get("/api/admin/shares", headers=admin_h)
        assert r.status_code == 200 and r.json()["total"] == 2, r.text
        r = await c.get("/api/admin/shares?active=true", headers=admin_h)
        assert r.json()["total"] == 1 and r.json()["items"][0]["is_active"] is True
        r = await c.get(f"/api/admin/shares?userId={ids['alice']}", headers=admin_h)
        assert r.json()["total"] == 2
        assert r.json()["items"][0]["creator_email"] == ALICE["email"]
        _ok("GET /admin/shares — 파일명·생성자 이메일 + active/userId 필터")

        # 4. 강제 비활성화 → 공개 메타 410 + audit 기록. 멱등.
        active_url = ids["share_active_url"]
        r = await c.get(f"/api/public/shares/{active_url}")  # 사전: 200
        assert r.status_code == 200, r.text
        r = await c.post(
            f"/api/admin/shares/{ids['share_active']}/disable", headers=admin_h
        )
        assert r.status_code == 200 and r.json()["is_active"] is False, r.text
        r = await c.get(f"/api/public/shares/{active_url}")  # 이후: 410 즉시 차단
        assert r.status_code == 410, r.text
        # 멱등 재호출.
        r = await c.post(
            f"/api/admin/shares/{ids['share_active']}/disable", headers=admin_h
        )
        assert r.status_code == 200, r.text
        _ok("POST /admin/shares/{id}/disable — 강제 비활성화 후 공개 410, 멱등")

        async with SessionFactory() as s:
            n = (
                await s.execute(
                    select(func.count()).select_from(AuditLog).where(
                        AuditLog.action == "share.force_disable",
                        AuditLog.target_type == "share",
                        AuditLog.target_id == ids["share_active"],
                    )
                )
            ).scalar_one()
        assert n == 1, f"audit rows={n} (멱등이므로 1 이어야 함)"
        _ok("audit_logs — share.force_disable 1건(멱등 재호출은 미기록)")

        # 5. audit-logs 필터 조회.
        r = await c.get("/api/admin/audit-logs?action=share.force_disable", headers=admin_h)
        assert r.status_code == 200 and r.json()["total"] == 1, r.text
        log = r.json()["items"][0]
        assert log["actor_email"] == ADMIN["email"]
        assert log["target_type"] == "share"
        r = await c.get(
            f"/api/admin/audit-logs?targetType=share&actorId={ids['admin']}", headers=admin_h
        )
        assert r.json()["total"] == 1
        r = await c.get("/api/admin/audit-logs?targetType=user", headers=admin_h)
        assert r.json()["total"] == 0  # 이 시나리오에는 user 대상 감사가 없음
        _ok("GET /admin/audit-logs — action/targetType/actorId 필터 + actor 이메일 join")

        # 6. lookup — 정확 일치 200 / 부분·미존재 404 / 비활성(pending) 404.
        r = await c.get("/api/users/lookup", params={"email": BOB["email"]}, headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.json() == {"id": ids["bob"], "email": BOB["email"], "display_name": "Bob"}
        # 대소문자/공백 정규화.
        r = await c.get(
            "/api/users/lookup", params={"email": "  BOB@Example.com "}, headers=alice_h
        )
        assert r.status_code == 200 and r.json()["id"] == ids["bob"]
        # 부분 일치 금지.
        r = await c.get("/api/users/lookup", params={"email": "bob@example"}, headers=alice_h)
        assert r.status_code in (404, 422), r.text
        # 비활성(inactive) 사용자는 조회 불가.
        r = await c.get("/api/users/lookup", params={"email": CAROL["email"]}, headers=alice_h)
        assert r.status_code == 404, r.text
        # 미인증 401.
        r = await c.get("/api/users/lookup", params={"email": BOB["email"]})
        assert r.status_code == 401, r.text
        _ok("GET /users/lookup — 정확 일치 200 / 정규화 / 부분·inactive·미인증 차단")

        # 6b. search — 이름/이메일 부분 일치 목록 (그룹 초대 클릭 선택 UX).
        # 부분 일치 200(active Bob), 자기 자신·inactive 제외, 최소 2자 강제, 미인증 401.
        r = await c.get("/api/users/search", params={"q": "Bob"}, headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.json() == [
            {"id": ids["bob"], "email": BOB["email"], "display_name": "Bob"}
        ]
        # 이메일 조각으로도 부분 일치.
        r = await c.get("/api/users/search", params={"q": "bob@ex"}, headers=alice_h)
        assert r.status_code == 200 and [u["id"] for u in r.json()] == [ids["bob"]]
        # 자기 자신은 결과에서 제외.
        r = await c.get("/api/users/search", params={"q": "alice"}, headers=alice_h)
        assert r.status_code == 200 and r.json() == [], r.text
        # 비활성(inactive) 사용자는 검색되지 않음.
        r = await c.get("/api/users/search", params={"q": "carol"}, headers=alice_h)
        assert r.status_code == 200 and r.json() == [], r.text
        # 최소 2자 미만은 422.
        r = await c.get("/api/users/search", params={"q": "b"}, headers=alice_h)
        assert r.status_code == 422, r.text
        # 미인증 401.
        r = await c.get("/api/users/search", params={"q": "bob"})
        assert r.status_code == 401, r.text
        _ok("GET /users/search — 부분 일치 200 / 자기·inactive 제외 / 최소 2자 / 미인증 차단")

        # 7. 로그인 rate limit — 6회째 429 (Retry-After), 다른 IP 독립.
        settings.rate_limit_login_per_min = 5
        await redis_client.flushdb()  # rl: 카운터 초기화.
        rl_ip = "10.9.9.1"
        codes = []
        for _ in range(6):
            r = await c.post(
                "/api/auth/login",
                json={"email": ALICE["email"], "password": "wrong-password"},
                headers={"X-Real-IP": rl_ip},
            )
            codes.append(r.status_code)
        assert codes[:5] == [401, 401, 401, 401, 401], codes  # 5회는 통과(인증 실패지만 카운트)
        assert codes[5] == 429, codes
        assert int(r.headers["Retry-After"]) > 0
        _ok("로그인 5회 후 6회째 429 + Retry-After")

        # 다른 IP 는 독립 카운트.
        r = await c.post(
            "/api/auth/login",
            json={"email": ALICE["email"], "password": "wrong-password"},
            headers={"X-Real-IP": "10.9.9.2"},
        )
        assert r.status_code == 401, r.text  # 429 아님 — 독립 버킷
        _ok("다른 IP(X-Real-IP) 독립 카운트 — 429 아님")

        # 8. admin 파일 내용 엔드포인트 부재 (PRD 3.6.4).
        settings.rate_limit_login_per_min = 1000
        for path in (
            f"/api/admin/files/{ids['f1']}/download",
            f"/api/admin/files/{ids['f1']}/content",
            f"/api/admin/shares/{ids['share_inactive']}/download",
        ):
            r = await c.get(path, headers=admin_h)
            assert r.status_code == 404, (
                f"{path} -> {r.status_code} (내용 접근 엔드포인트 부재여야)"
            )
        _ok("admin 네임스페이스에 파일 내용 다운로드 엔드포인트 부재 (3.6.4)")

    await engine.dispose()
    await redis_client.aclose()
    print("\nAdmin/rate-limit/lookup 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
