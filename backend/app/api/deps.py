"""공통 API 의존성 — 인증/인가 (PRD 10장).

- get_current_user: access JWT 검증 + 매 요청 DB 에서 status='active' 확인
  (비활성화/거절 계정은 access 토큰 유효 기간 내에도 즉시 차단).
- require_admin: role='admin' 확인. /api/admin/* 라우터 전체에 일괄 적용.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.security import TokenError, decode_token
from app.models import User
from app.models.enums import UserRole, UserStatus
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
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다.",
        )
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]

# 라우트에서 세션/Redis 를 짧게 주입하기 위한 별칭.
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]
