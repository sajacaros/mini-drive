"""공통 API 의존성 — 인증/인가 (PRD 10장).

- get_current_user: access JWT 검증 + 매 요청 DB 에서 status='active' 확인
  (비활성화/거절 계정은 access 토큰 유효 기간 내에도 즉시 차단).
- require_admin: role='admin' 확인. /api/admin/* 라우터 전체에 일괄 적용.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.metrics import observe_rate_limit_rejection
from app.core.ratelimit import check_rate_limit
from app.core.redis import get_redis
from app.core.security import TokenError, decode_token
from app.models import User
from app.models.enums import ADMIN_ROLES, UserStatus
from app.services.users import get_user_by_id

# auto_error=False 로 두어 누락 시 401(WWW-Authenticate) 을 직접 통일한다.
_bearer_scheme = HTTPBearer(auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="인증 자격 증명이 유효하지 않습니다.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise _CREDENTIALS_EXC

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        user_id = int(payload["sub"])
    except (TokenError, KeyError, ValueError) as exc:
        raise _CREDENTIALS_EXC from exc

    user = await get_user_by_id(session, user_id)
    if user is None:
        raise _CREDENTIALS_EXC

    # 매 요청 status 확인 — 비활성화/거절 즉시 차단 (PRD 10장 토큰 폐기).
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화되었거나 승인되지 않은 계정입니다.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(current_user: CurrentUser) -> User:
    # admin/super_admin 모두 관리자 라우터 통과 (super_admin 은 admin 의 상위).
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다.",
        )
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]

# 라우트에서 세션/Redis 를 짧게 주입하기 위한 별칭.
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


# --- Rate limiting 의존성 (PRD 10장) ----------------------------------------


def client_ip(request: Request) -> str:
    """요청 IP 추출. 게이트웨이가 설정하는 X-Real-IP 우선, 없으면 소켓 주소.

    backend 는 nginx 뒤에서만 노출되므로(PRD 7장 expose) X-Real-IP 를 신뢰한다.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _raise_429(retry_after: int, scope: str) -> None:
    observe_rate_limit_rejection(scope)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit_ip(scope: str, limit_attr: str) -> Callable[..., Awaitable[None]]:
    """IP 당 rate limit 의존성 (비인증 엔드포인트: 로그인/가입/refresh).

    한도(limit)는 요청 시점에 Settings 에서 읽어 환경변수로 조정 가능하게 한다.
    """

    async def dependency(
        request: Request,
        redis: RedisClient,
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        result = await check_rate_limit(
            redis,
            scope=scope,
            identifier=client_ip(request),
            limit=getattr(settings, limit_attr),
            window_seconds=settings.rate_limit_window_seconds,
        )
        if not result.allowed:
            _raise_429(result.retry_after, scope)

    return dependency


def rate_limit_user(scope: str, limit_attr: str) -> Callable[..., Awaitable[None]]:
    """user 당 rate limit 의존성 (인증 엔드포인트: 업로드/이메일 조회).

    이미 인증된 사용자를 식별자로 쓰므로 CurrentUser 를 함께 의존한다(FastAPI 가 캐시).
    """

    async def dependency(
        current_user: CurrentUser,
        redis: RedisClient,
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        result = await check_rate_limit(
            redis,
            scope=scope,
            identifier=str(current_user.id),
            limit=getattr(settings, limit_attr),
            window_seconds=settings.rate_limit_window_seconds,
        )
        if not result.allowed:
            _raise_429(result.retry_after, scope)

    return dependency
