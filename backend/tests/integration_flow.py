"""통합 검증 스크립트 (pytest 미수집 — 실제 postgres+redis 필요).

셋업 위저드 → 코드 가입(즉시 active) → 로그인 → me → refresh 회전 → 재사용 감지 전체 폐기 →
logout → 자기 admin 해제 거부 → 비활성화 즉시 차단 플로우를 httpx(ASGITransport)로 검증한다.
DATABASE_URL / REDIS_URL 환경변수로 실제 서비스에 연결한다. `python -m tests.integration_flow`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import File, User
from tests._bootstrap import ADMIN_EMAIL, ADMIN_PASSWORD, login, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    # 이전 실행의 refresh 토큰 잔여 제거.
    await redis_client.flushdb()


async def scenario() -> None:
    await _reset_schema()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # 0. 셋업 필요 상태 → 셋업 위저드로 admin + 초기 코드 생성
        r = await c.get("/api/setup/status")
        assert r.status_code == 200 and r.json()["setup_required"] is True, r.text
        _ok("셋업 필요 상태 확인 (setup_required=true)")

        admin_h, code = await setup_admin(c)
        _ok("셋업 위저드로 admin + 초기 가입 코드 생성")

        # 셋업 완료 후 재호출 차단 (403)
        r = await c.post(
            "/api/setup",
            json={"admin_email": "other@example.com", "admin_password": "Other1!pass"},
        )
        assert r.status_code == 403, r.text
        _ok("셋업 재진입 차단 (403)")

        # admin 도 루트 폴더를 가진다.
        async with SessionFactory() as session:
            admin_row = (
                await session.execute(select(User).where(User.email == ADMIN_EMAIL))
            ).scalar_one()
            root = (
                await session.execute(
                    select(File).where(
                        File.user_id == admin_row.id, File.parent_folder_id.is_(None)
                    )
                )
            ).scalar_one()
            assert root.is_folder is True
        _ok("admin 루트 폴더 생성 확인")

        # 1. 코드 가입 → 즉시 active
        r = await c.post("/api/auth/register", json={**ALICE, "signup_code": code})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "active"
        alice_id = r.json()["id"]
        _ok("코드 가입 → 즉시 active 생성")

        # 약한 비밀번호 거부 (422)
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "weak@example.com",
                "password": "weakpass",
                "display_name": "",
                "signup_code": code,
            },
        )
        assert r.status_code == 422, r.text
        _ok("약한 비밀번호 register 거부 (422)")

        # 잘못된 가입 코드 거부 (400)
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "nocode@example.com",
                "password": "Passw0rd!",
                "display_name": "",
                "signup_code": "definitely-not-a-real-code",
            },
        )
        assert r.status_code == 400, r.text
        _ok("잘못된 가입 코드 거부 (400)")

        # 중복 이메일 거부 (409)
        r = await c.post("/api/auth/register", json={**ALICE, "signup_code": code})
        assert r.status_code == 409, r.text
        _ok("중복 이메일 register 거부 (409)")

        # alice 루트 폴더가 가입 시 생성되었는지 확인
        async with SessionFactory() as session:
            root = (
                await session.execute(
                    select(File).where(
                        File.user_id == alice_id, File.parent_folder_id.is_(None)
                    )
                )
            ).scalar_one()
            assert root.is_folder is True
        _ok("가입 시 alice 루트 폴더 생성 확인")

        # 2. alice 즉시 로그인 성공 (승인 단계 없음)
        r = await c.post(
            "/api/auth/login",
            json={"email": ALICE["email"], "password": ALICE["password"]},
        )
        assert r.status_code == 200, r.text
        tokens = r.json()
        alice_h = {"Authorization": f"Bearer {tokens['access_token']}"}
        refresh1 = tokens["refresh_token"]
        _ok("가입 후 alice 즉시 로그인 성공")

        # 3. me
        r = await c.get("/api/auth/me", headers=alice_h)
        assert r.status_code == 200 and r.json()["email"] == ALICE["email"], r.text
        assert "password_hash" not in r.json()
        _ok("GET /me (password_hash 미포함)")

        # 비관리자 admin 라우트 접근 차단 (403)
        r = await c.get("/api/admin/users", headers=alice_h)
        assert r.status_code == 403, r.text
        _ok("비관리자 admin 접근 차단 (403)")

        # 무인증 admin 접근 (401)
        r = await c.get("/api/admin/users")
        assert r.status_code == 401, r.text
        _ok("무인증 admin 접근 차단 (401)")

        # 4. refresh 회전
        r = await c.post("/api/auth/refresh", json={"refresh_token": refresh1})
        assert r.status_code == 200, r.text
        refresh2 = r.json()["refresh_token"]
        assert refresh2 != refresh1
        _ok("refresh 회전 (새 토큰 발급)")

        # 재사용 감지 → 이전 refresh 401 + 전체 세션 폐기
        r = await c.post("/api/auth/refresh", json={"refresh_token": refresh1})
        assert r.status_code == 401, r.text
        _ok("이전 refresh 재사용 감지 (401)")

        # 재사용 감지로 refresh2 도 폐기됨
        r = await c.post("/api/auth/refresh", json={"refresh_token": refresh2})
        assert r.status_code == 401, r.text
        _ok("재사용 감지 시 전체 세션 폐기 확인")

        # 5. logout 플로우
        r = await c.post(
            "/api/auth/login",
            json={"email": ALICE["email"], "password": ALICE["password"]},
        )
        tokens = r.json()
        alice_h = {"Authorization": f"Bearer {tokens['access_token']}"}
        r = await c.post(
            "/api/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers=alice_h,
        )
        assert r.status_code == 204, r.text
        r = await c.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 401, r.text
        _ok("logout 후 refresh 폐기 확인 (401)")

        # 6. admin 자기 권한 해제 거부 (마지막 admin 잠금 방지)
        admin_h = await login(c, ADMIN_EMAIL, ADMIN_PASSWORD)
        async with SessionFactory() as session:
            admin_row = (
                await session.execute(select(User).where(User.email == ADMIN_EMAIL))
            ).scalar_one()
        r = await c.patch(
            f"/api/admin/users/{admin_row.id}",
            json={"role": "user"},
            headers=admin_h,
        )
        assert r.status_code == 400, r.text
        _ok("자기 자신 admin 권한 해제 거부 (400)")

        # 7. 비활성화 즉시 차단: alice 를 inactive 로 전환 후 me 접근 차단
        r = await c.post(
            "/api/auth/login",
            json={"email": ALICE["email"], "password": ALICE["password"]},
        )
        alice_tokens = r.json()
        alice_h = {"Authorization": f"Bearer {alice_tokens['access_token']}"}
        r = await c.patch(
            f"/api/admin/users/{alice_id}",
            json={"status": "inactive"},
            headers=admin_h,
        )
        assert r.status_code == 200 and r.json()["status"] == "inactive", r.text
        # 기존 access 토큰은 유효 기간이 남았어도 매 요청 status 확인으로 차단
        r = await c.get("/api/auth/me", headers=alice_h)
        assert r.status_code == 403, r.text
        _ok("비활성화 즉시 차단 (access 유효기간 내 403)")

        # 비활성 계정은 로그인도 차단 (403)
        r = await c.post(
            "/api/auth/login",
            json={"email": ALICE["email"], "password": ALICE["password"]},
        )
        assert r.status_code == 403, r.text
        _ok("비활성 계정 로그인 차단 (403)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
