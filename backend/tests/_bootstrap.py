"""통합 테스트 부트스트랩 헬퍼 — 셋업 위저드 경유 admin + 코드 가입 (가입 코드제).

가입 승인제 폐지(2026-07-19)로 admin 은 POST /api/setup 으로 만들고, 신규 사용자는 관리자가
발급한 가입 코드로 즉시 active 가입한다. 각 통합 스크립트가 공유한다.
"""

from __future__ import annotations

import httpx

# 셋업 위저드로 만드는 첫 admin 자격 증명 (테스트 고정값). 비밀번호 정책을 만족해야 한다.
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Adm1n!pass"


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def login(
    c: httpx.AsyncClient, email: str, password: str, *, real_ip: str | None = None
) -> dict[str, str]:
    headers = {"X-Real-IP": real_ip} if real_ip else None
    r = await c.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return auth_header(r.json()["access_token"])


async def setup_admin(c: httpx.AsyncClient) -> tuple[dict[str, str], str]:
    """빈 DB 에 셋업 위저드로 첫 admin + 초기 가입 코드를 만들고 (admin 헤더, 코드) 를 반환한다."""
    r = await c.post(
        "/api/setup",
        json={"admin_email": ADMIN_EMAIL, "admin_password": ADMIN_PASSWORD},
    )
    assert r.status_code == 201, r.text
    code = r.json()["signup_code"]
    admin_h = await login(c, ADMIN_EMAIL, ADMIN_PASSWORD)
    return admin_h, code


async def register_active(
    c: httpx.AsyncClient, code: str, creds: dict[str, str]
) -> tuple[dict[str, str], int]:
    """가입 코드로 즉시 active 가입 후 로그인. (헤더, user_id) 반환."""
    r = await c.post("/api/auth/register", json={**creds, "signup_code": code})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    h = await login(c, creds["email"], creds["password"])
    return h, uid
