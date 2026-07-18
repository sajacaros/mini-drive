"""일반 사용자 조회 라우터 (그룹 초대 UX 개선).

`GET /api/users/lookup?email=` — 인증된 active 사용자 누구나 호출 가능. **정확히 일치하는**
active 사용자만 반환한다. 부분 검색/목록은 이메일 열거 방지를 위해 제공하지 않는다
(사내 서비스라 정확 일치 조회는 허용). rate limit 대상(user 당 20회/분).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit_user
from app.schemas.users import UserLookupResponse
from app.services.users import get_active_user_by_email

router = APIRouter()


@router.get(
    "/lookup",
    response_model=UserLookupResponse,
    dependencies=[Depends(rate_limit_user("lookup", "rate_limit_lookup_per_min"))],
)
async def lookup_user(
    _current: CurrentUser,
    session: DbSession,
    email: Annotated[str, Query(max_length=255)],
) -> UserLookupResponse:
    """이메일 정확 일치 active 사용자 조회. 없으면 404 (부분 검색 금지)."""
    normalized = email.strip().lower()
    user = await get_active_user_by_email(session, normalized)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 이메일의 사용자를 찾을 수 없습니다.",
        )
    return UserLookupResponse.model_validate(user)
