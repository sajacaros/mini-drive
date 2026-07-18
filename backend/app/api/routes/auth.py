"""인증 라우터 (PRD 6.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    RedisClient,
    rate_limit_ip,
)
from app.core.security import (
    PasswordPolicyError,
    TokenError,
    decode_token,
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.models import User
from app.models.enums import UserRole, UserStatus
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from app.services.auth import RefreshError, issue_token_pair, rotate_refresh_token
from app.services.tokens import revoke_refresh_token
from app.services.users import get_user_by_email

router = APIRouter()


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_ip("register", "rate_limit_register_per_min"))],
)
async def register(payload: RegisterRequest, session: DbSession) -> RegisterResponse:
    """회원가입 — status='pending' 으로 생성. 관리자 승인 후 로그인 가능 (PRD 3.1)."""
    try:
        validate_password_policy(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if await get_user_by_email(session, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 이메일입니다."
        )

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role=UserRole.USER,
        status=UserStatus.PENDING,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return RegisterResponse(id=user.id, email=user.email, status=user.status)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_ip("login", "rate_limit_login_per_min"))],
)
async def login(payload: LoginRequest, session: DbSession, redis: RedisClient) -> TokenResponse:
    """로그인 — status='active' 만 허용. 성공 시 access/refresh JWT 발급 (PRD 6.1, 10장)."""
    user = await get_user_by_email(session, payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    if user.status == UserStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 승인 대기 중인 계정입니다. 승인 후 이용 가능합니다.",
        )
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화되었거나 거절된 계정입니다.",
        )

    return await issue_token_pair(redis, user.id)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_ip("refresh", "rate_limit_refresh_per_min"))],
)
async def refresh(
    payload: RefreshRequest, session: DbSession, redis: RedisClient
) -> TokenResponse:
    """refresh 토큰 회전 — 재사용 감지 시 전체 세션 폐기 (PRD 10장)."""
    try:
        user_id, tokens = await rotate_refresh_token(redis, payload.refresh_token)
    except RefreshError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    # 회전 직후에도 계정 상태를 재확인 — 비활성화된 계정의 갱신 차단.
    user = await session.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화되었거나 승인되지 않은 계정입니다.",
        )
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest, current_user: CurrentUser, redis: RedisClient
) -> Response:
    """로그아웃 — Redis 의 해당 refresh 토큰 폐기 (PRD 6.1)."""
    try:
        token_payload = decode_token(payload.refresh_token, expected_type="refresh")
        jti = token_payload["jti"]
        token_user_id = int(token_payload["sub"])
    except (TokenError, KeyError, ValueError):
        # 이미 무효한 토큰이면 멱등하게 성공 처리.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 자신의 토큰만 폐기 가능.
    if token_user_id == current_user.id:
        await revoke_refresh_token(redis, current_user.id, jti)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUser) -> User:
    """현재 사용자 정보 (password_hash 제외) — PRD 6.1."""
    return current_user
