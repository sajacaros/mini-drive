"""통합 검증 스크립트 — 셋업 위저드 + 가입 코드제 (pytest 미수집, 실제 postgres+redis 필요).

빈 DB → setup status 필요 → setup 완료 → 재호출 403 → 코드 가입 → 즉시 로그인 →
코드 만료/소진/비활성 거부 → admin 코드 CRUD + 감사 로그 를 검증한다.
env: DATABASE_URL / REDIS_URL. 실행: `python -m tests.integration_setup`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport
from sqlalchemy import select, text

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import AppSetting, AuditLog, User
from app.models.user import DEFAULT_MAX_STORAGE
from tests._bootstrap import ADMIN_EMAIL, ADMIN_PASSWORD, login
from tests._dbreset import stamp_alembic_head

CUSTOM_QUOTA = 5 * 1024 * 1024 * 1024  # 5GB


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()


async def _expire_code(code: str) -> None:
    async with SessionFactory() as s:
        await s.execute(
            text("UPDATE signup_codes SET expires_at = :ts WHERE code = :c"),
            {"ts": datetime.now(UTC) - timedelta(hours=1), "c": code},
        )
        await s.commit()


async def scenario() -> None:  # noqa: C901, PLR0915 - 순차 시나리오 한 흐름
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # 1. 빈 DB → 셋업 필요
        r = await c.get("/api/setup/status")
        assert r.status_code == 200, r.text
        assert r.json() == {"setup_required": True, "admin_exists": False}, r.json()
        _ok("빈 DB → setup_required=true, admin_exists=false")

        # 2. 셋업 실행 — admin + 직접 지정한 초기 코드 + 커스텀 기본 할당량
        r = await c.post(
            "/api/setup",
            json={
                "admin_email": ADMIN_EMAIL,
                "admin_password": ADMIN_PASSWORD,
                "signup_code": "onboarding-2026",
                "default_max_storage": CUSTOM_QUOTA,
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["signup_code"] == "onboarding-2026", r.text
        assert r.json()["default_max_storage"] == CUSTOM_QUOTA
        _ok("셋업 완료 (admin + 초기 코드 + 커스텀 할당량)")

        # app_settings 기록 확인
        async with SessionFactory() as s:
            completed = await s.get(AppSetting, "setup_completed")
            quota = await s.get(AppSetting, "default_max_storage")
            assert completed is not None and completed.value is True
            assert quota is not None and int(quota.value) == CUSTOM_QUOTA
        _ok("app_settings(setup_completed/default_max_storage) 기록 확인")

        # 3. 셋업 후 status → 불필요
        r = await c.get("/api/setup/status")
        assert r.json() == {"setup_required": False, "admin_exists": True}, r.json()
        _ok("셋업 후 setup_required=false, admin_exists=true")

        # 4. 셋업 재호출 → 403
        r = await c.post(
            "/api/setup",
            json={"admin_email": "evil@example.com", "admin_password": "Evil1!pass"},
        )
        assert r.status_code == 403, r.text
        _ok("셋업 재진입 차단 (403)")

        admin_h = await login(c, ADMIN_EMAIL, ADMIN_PASSWORD)

        # 5. 초기 코드로 가입 → 즉시 active + 커스텀 할당량 적용
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "alice@example.com",
                "password": "Passw0rd!",
                "display_name": "Alice",
                "signup_code": "onboarding-2026",
            },
        )
        assert r.status_code == 201 and r.json()["status"] == "active", r.text
        alice_id = r.json()["id"]
        async with SessionFactory() as s:
            alice = (await s.execute(select(User).where(User.id == alice_id))).scalar_one()
            assert alice.max_storage == CUSTOM_QUOTA, alice.max_storage
        _ok("초기 코드 가입 → 즉시 active + 기본 할당량(5GB) 적용")

        r = await c.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "Passw0rd!"},
        )
        assert r.status_code == 200, r.text
        _ok("가입 직후 즉시 로그인 성공")

        # 6. admin 코드 발급 (max_uses=1) → 사용 후 소진 거부
        r = await c.post(
            "/api/admin/signup-codes",
            headers=admin_h,
            json={"memo": "one-shot", "max_uses": 1},
        )
        assert r.status_code == 201, r.text
        one_shot = r.json()["code"]
        one_shot_id = r.json()["id"]
        assert r.json()["use_count"] == 0 and r.json()["is_active"] is True
        _ok("admin 코드 발급 (max_uses=1, 자동 생성 코드)")

        r = await c.post(
            "/api/auth/register",
            json={
                "email": "bob@example.com",
                "password": "Passw0rd!",
                "display_name": "Bob",
                "signup_code": one_shot,
            },
        )
        assert r.status_code == 201, r.text
        _ok("one-shot 코드로 1회 가입 성공")

        r = await c.post(
            "/api/auth/register",
            json={
                "email": "carol@example.com",
                "password": "Passw0rd!",
                "display_name": "Carol",
                "signup_code": one_shot,
            },
        )
        assert r.status_code == 400, r.text
        assert "소진" in r.json()["detail"], r.json()
        _ok("소진된 코드 가입 거부 (400)")

        # 7. 목록 조회 — use_count 반영
        r = await c.get("/api/admin/signup-codes", headers=admin_h)
        assert r.status_code == 200, r.text
        by_id = {item["id"]: item for item in r.json()["items"]}
        assert by_id[one_shot_id]["use_count"] == 1, by_id[one_shot_id]
        _ok("코드 목록 조회 — use_count=1 반영")

        # 8. PATCH 로 비활성화 → 비활성 코드 가입 거부
        r = await c.patch(
            f"/api/admin/signup-codes/{one_shot_id}",
            headers=admin_h,
            json={"is_active": False},
        )
        assert r.status_code == 200 and r.json()["is_active"] is False, r.text
        _ok("PATCH 코드 비활성화")

        # 별도 활성 코드를 만들어 비활성/만료를 각각 검증
        r = await c.post(
            "/api/admin/signup-codes", headers=admin_h, json={"memo": "to-expire"}
        )
        expiring = r.json()["code"]
        expiring_id = r.json()["id"]

        r = await c.post(
            "/api/auth/register",
            json={
                "email": "dave@example.com",
                "password": "Passw0rd!",
                "display_name": "Dave",
                "signup_code": one_shot,
            },
        )
        assert r.status_code == 400 and "비활성" in r.json()["detail"], r.json()
        _ok("비활성 코드 가입 거부 (400)")

        # 9. 만료 코드 거부
        await _expire_code(expiring)
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "dave@example.com",
                "password": "Passw0rd!",
                "display_name": "Dave",
                "signup_code": expiring,
            },
        )
        assert r.status_code == 400 and "만료" in r.json()["detail"], r.json()
        _ok("만료 코드 가입 거부 (400)")

        # 10. PATCH 로 재활성화 + 만료 해제 → 다시 가입 가능
        r = await c.patch(
            f"/api/admin/signup-codes/{expiring_id}",
            headers=admin_h,
            json={"expires_at": None},
        )
        assert r.status_code == 200, r.text
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "dave@example.com",
                "password": "Passw0rd!",
                "display_name": "Dave",
                "signup_code": expiring,
            },
        )
        assert r.status_code == 201, r.text
        _ok("만료 해제(PATCH expires_at=null) 후 재가입 성공")

        # 11. 존재하지 않는 코드 → 400
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "erin@example.com",
                "password": "Passw0rd!",
                "display_name": "Erin",
                "signup_code": "no-such-code",
            },
        )
        assert r.status_code == 400 and "존재하지" in r.json()["detail"], r.json()
        _ok("존재하지 않는 코드 가입 거부 (400)")

        # 12. 없는 코드 PATCH → 404
        r = await c.patch(
            "/api/admin/signup-codes/999999", headers=admin_h, json={"is_active": True}
        )
        assert r.status_code == 404, r.text
        _ok("없는 코드 PATCH → 404")

        # 13. 감사 로그 — signup_code.create / signup_code.update 기록 확인
        r = await c.get(
            "/api/admin/audit-logs",
            headers=admin_h,
            params={"targetType": "signup_code"},
        )
        assert r.status_code == 200, r.text
        actions = {item["action"] for item in r.json()["items"]}
        assert "signup_code.create" in actions, actions
        assert "signup_code.update" in actions, actions
        _ok("감사 로그에 signup_code.create/update 기록 확인")

        # 감사 로그 총량 교차 확인 (DB)
        async with SessionFactory() as s:
            n = (
                await s.execute(
                    select(AuditLog).where(AuditLog.target_type == "signup_code")
                )
            ).scalars().all()
            assert len(n) >= 4, len(n)  # create x3 (setup+2) + update x2
        _ok(f"signup_code 감사 로그 {len(n)}건 기록")

    # 셋업 전 기본 할당량 fallback = 10GB 확인 (모델 상수).
    assert DEFAULT_MAX_STORAGE == 10_737_418_240
    _ok("셋업 전 기본 할당량 fallback=10GB 상수 확인")

    await engine.dispose()
    await redis_client.aclose()
    print("\n셋업/가입 코드제 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
