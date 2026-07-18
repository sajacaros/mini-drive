"""셋업 위저드 라우터 (PRD 3.6.2, 6.1). 무인증 — admin 존재 시 POST 는 403."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.core.security import PasswordPolicyError, validate_password_policy
from app.schemas.setup import SetupRequest, SetupResponse, SetupStatusResponse
from app.services.setup import (
    SetupError,
    admin_exists,
    is_setup_completed,
    perform_setup,
)

router = APIRouter()


@router.get("/status", response_model=SetupStatusResponse)
async def setup_status(session: DbSession) -> SetupStatusResponse:
    """셋업 필요 여부 (무인증) — admin 존재/셋업 완료 여부 (PRD 6.1)."""
    completed = await is_setup_completed(session)
    exists = await admin_exists(session)
    return SetupStatusResponse(setup_required=not completed, admin_exists=exists)


@router.post("", response_model=SetupResponse, status_code=status.HTTP_201_CREATED)
async def setup(payload: SetupRequest, session: DbSession) -> SetupResponse:
    """첫 부팅 셋업 — 첫 admin + 초기 가입 코드 + 기본 할당량. admin 존재 시 403 (PRD 3.6.2)."""
    try:
        validate_password_policy(payload.admin_password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        admin, code = await perform_setup(
            session,
            admin_email=payload.admin_email,
            admin_password=payload.admin_password,
            signup_code=payload.signup_code,
            default_max_storage=payload.default_max_storage,
        )
    except SetupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return SetupResponse(
        admin_email=admin.email,
        signup_code=code.code,
        default_max_storage=payload.default_max_storage,
    )
