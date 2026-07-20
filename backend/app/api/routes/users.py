"""일반 사용자 조회 라우터 (그룹 초대 UX 개선).

`GET /api/users/lookup?email=` — 인증된 active 사용자 누구나 호출 가능. **정확히 일치하는**
active 사용자만 반환한다. rate limit 대상(user 당 20회/분).

`GET /api/users/search?q=` — 이름/이메일 **부분 일치** active 사용자 목록(최소 2자, 상한 20,
자기 자신 제외). 클릭 방식 멤버 선택 UX 용. 사내 서비스라 부분 검색을 허용하되, 결과 상한·
최소 필드(id/email/display_name)·rate limit 으로 이메일 열거를 제한한다.

`PATCH /api/users/me` — 현재 사용자의 표시 이름 편집(프로필). 셋업 admin 이 기본 이름
"Administrator" 를 실명으로 바꾸는 등에 쓴다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, rate_limit_user
from app.models import User
from app.schemas.auth import UserResponse
from app.schemas.users import MeUpdateRequest, UserLookupResponse
from app.services.users import (
    get_active_user_by_email,
    search_active_users,
    update_display_name,
)

router = APIRouter()


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: MeUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> User:
    """현재 사용자의 프로필(표시 이름)을 수정한다 — 셋업 admin 등 이름 변경 경로."""
    return await update_display_name(session, current, payload.display_name)


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


@router.get(
    "/search",
    response_model=list[UserLookupResponse],
    dependencies=[Depends(rate_limit_user("user_search", "rate_limit_lookup_per_min"))],
)
async def search_users(
    current: CurrentUser,
    session: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=255)],
) -> list[User]:
    """이름/이메일 부분 일치 active 사용자 검색 (그룹 초대용, 자기 자신 제외).

    최소 2자. 이메일 열거를 제한하기 위해 결과 상한(20)과 rate limit 을 둔다. 정확 일치만
    반환하는 `/lookup` 과 달리 부분 일치를 허용하되, 상한·상세 필드 최소화로 노출을 제한한다.
    """
    normalized = q.strip()
    if len(normalized) < 2:
        return []
    return await search_active_users(
        session, normalized, limit=20, exclude_user_id=current.id
    )
