"""Admin 사용자 관리 라우터 (PRD 6.7). 전체 라우터에 require_admin 일괄 적용."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AdminUser, DbSession, require_admin
from app.schemas.admin import (
    AdminUserListResponse,
    AdminUserResponse,
    UserUpdateRequest,
)
from app.services.admin import (
    AdminActionError,
    approve_user,
    list_users,
    reject_user,
    update_user,
)

# 라우터 전체에 관리자 인가를 일괄 적용한다 (PRD 3.6.4).
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/users", response_model=AdminUserListResponse)
async def get_users(
    session: DbSession,
    status: Annotated[str | None, Query(description="상태 필터 (예: pending)")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminUserListResponse:
    """사용자 목록 (사용량 포함, password_hash 제외) — PRD 6.7."""
    items, total = await list_users(session, status, page, size)
    return AdminUserListResponse(
        items=[AdminUserResponse.model_validate(u) for u in items],
        total=total,
        page=page,
        size=size,
    )


@router.post("/users/{user_id}/approve", response_model=AdminUserResponse)
async def approve(user_id: int, admin: AdminUser, session: DbSession) -> AdminUserResponse:
    """가입 승인 (pending → active) + 루트 폴더 생성 (PRD 6.7)."""
    try:
        user = await approve_user(session, admin, user_id)
    except AdminActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return AdminUserResponse.model_validate(user)


@router.post("/users/{user_id}/reject", response_model=AdminUserResponse)
async def reject(user_id: int, admin: AdminUser, session: DbSession) -> AdminUserResponse:
    """가입 거절 (pending → rejected) (PRD 6.7)."""
    try:
        user = await reject_user(session, admin, user_id)
    except AdminActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return AdminUserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def patch_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: AdminUser,
    session: DbSession,
) -> AdminUserResponse:
    """활성/비활성 전환, 할당량 조정, role 변경 (PRD 6.7)."""
    try:
        user = await update_user(
            session,
            admin,
            user_id,
            new_status=payload.status,
            new_role=payload.role,
            new_max_storage=payload.max_storage,
        )
    except AdminActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return AdminUserResponse.model_validate(user)
