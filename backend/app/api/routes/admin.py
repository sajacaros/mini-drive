"""Admin 사용자 관리 라우터 (PRD 6.7). 전체 라우터에 require_admin 일괄 적용."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AdminUser, DbSession, require_admin
from app.schemas.admin import (
    AdminAuditLogListResponse,
    AdminAuditLogResponse,
    AdminGroupListResponse,
    AdminGroupResponse,
    AdminShareListResponse,
    AdminShareResponse,
    AdminStatsResponse,
    AdminUserListResponse,
    AdminUserResponse,
    UserUpdateRequest,
)
from app.schemas.signup_codes import (
    SignupCodeCreateRequest,
    SignupCodeListResponse,
    SignupCodeResponse,
    SignupCodeUpdateRequest,
)
from app.services.admin import (
    AdminActionError,
    force_disable_share,
    get_share_detail,
    get_stats,
    list_audit_logs,
    list_groups,
    list_shares,
    list_users,
    update_user,
)
from app.services.signup_codes import (
    SignupCodeError,
    create_signup_code,
    list_signup_codes,
    update_signup_code,
)

# 라우터 전체에 관리자 인가를 일괄 적용한다 (PRD 3.6.4).
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/users", response_model=AdminUserListResponse)
async def get_users(
    session: DbSession,
    status: Annotated[
        str | None, Query(description="상태 필터 (active/inactive)")
    ] = None,
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


# --- 가입 코드 관리 (PRD 6.7) ------------------------------------------------


@router.post(
    "/signup-codes",
    response_model=SignupCodeResponse,
    status_code=201,
)
async def create_code(
    payload: SignupCodeCreateRequest, admin: AdminUser, session: DbSession
) -> SignupCodeResponse:
    """가입 코드 발급 (memo/expires_at/max_uses, code 미지정 시 자동 생성) — PRD 6.7."""
    code = await create_signup_code(
        session,
        admin.id,
        memo=payload.memo,
        expires_at=payload.expires_at,
        max_uses=payload.max_uses,
        code=payload.code,
    )
    await session.commit()
    await session.refresh(code)
    return SignupCodeResponse.model_validate(code)


@router.get("/signup-codes", response_model=SignupCodeListResponse)
async def get_codes(
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SignupCodeListResponse:
    """가입 코드 목록·사용 현황 조회 (최신순) — PRD 6.7."""
    items, total = await list_signup_codes(session, page, size)
    return SignupCodeListResponse(
        items=[SignupCodeResponse.model_validate(c) for c in items],
        total=total,
        page=page,
        size=size,
    )


@router.patch("/signup-codes/{code_id}", response_model=SignupCodeResponse)
async def patch_code(
    code_id: int,
    payload: SignupCodeUpdateRequest,
    admin: AdminUser,
    session: DbSession,
) -> SignupCodeResponse:
    """가입 코드 수정 (비활성화/재활성화, 만료·횟수·메모 조정) — PRD 6.7."""
    try:
        code = await update_signup_code(
            session, admin.id, code_id, payload.model_dump(exclude_unset=True)
        )
    except SignupCodeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await session.commit()
    await session.refresh(code)
    return SignupCodeResponse.model_validate(code)


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


# --- 전체 그룹 조회 (PRD 6.7) ------------------------------------------------


@router.get("/groups", response_model=AdminGroupListResponse)
async def get_groups(
    session: DbSession,
    include_inactive: Annotated[
        bool, Query(description="비활성 그룹 포함 여부")
    ] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminGroupListResponse:
    """전체 그룹 목록 — owner 정보·멤버 수·소유 파일 수 포함 (PRD 6.7)."""
    items, total = await list_groups(session, include_inactive, page, size)
    return AdminGroupListResponse(
        items=[AdminGroupResponse(**i) for i in items],
        total=total,
        page=page,
        size=size,
    )


# --- 전체 공유 링크 조회 + 강제 비활성화 (PRD 6.7) ---------------------------


@router.get("/shares", response_model=AdminShareListResponse)
async def get_shares(
    session: DbSession,
    active: Annotated[bool | None, Query(description="활성 상태 필터")] = None,
    user_id: Annotated[int | None, Query(alias="userId")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminShareListResponse:
    """전체 공유 링크 목록 — 파일명·생성자 이메일·다운로드 수·만료·활성 (PRD 6.7)."""
    items, total = await list_shares(session, active, user_id, page, size)
    return AdminShareListResponse(
        items=[AdminShareResponse(**i) for i in items],
        total=total,
        page=page,
        size=size,
    )


@router.post("/shares/{share_id}/disable", response_model=AdminShareResponse)
async def disable_share(
    share_id: int, admin: AdminUser, session: DbSession
) -> AdminShareResponse:
    """공유 링크 강제 비활성화 + audit_log(share.force_disable). 멱등 (PRD 6.7)."""
    try:
        share = await force_disable_share(session, admin, share_id)
    except AdminActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    detail = await get_share_detail(session, share.id)
    assert detail is not None  # 방금 비활성화한 링크이므로 항상 존재.
    return AdminShareResponse(**detail)


# --- 대시보드 통계 (PRD 6.7) -------------------------------------------------


@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(session: DbSession) -> AdminStatsResponse:
    """인스턴스 대시보드 통계 — 단일 응답 (PRD 6.7). 파일 내용 접근 없음 (3.6.4)."""
    stats = await get_stats(session)
    return AdminStatsResponse(**stats)


# --- 감사 로그 조회 (PRD 5.9, 6.7) -------------------------------------------


@router.get("/audit-logs", response_model=AdminAuditLogListResponse)
async def get_audit_logs(
    session: DbSession,
    actor_id: Annotated[int | None, Query(alias="actorId")] = None,
    target_type: Annotated[str | None, Query(alias="targetType")] = None,
    action: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminAuditLogListResponse:
    """감사 로그 조회 — actor 이메일 join, 필터(actorId/targetType/action/from/to) (PRD 6.7)."""
    items, total = await list_audit_logs(
        session, actor_id, target_type, action, date_from, date_to, page, size
    )
    return AdminAuditLogListResponse(
        items=[AdminAuditLogResponse(**i) for i in items],
        total=total,
        page=page,
        size=size,
    )
