"""일반 사용자 조회 라우터 (그룹 초대 UX 개선).

`GET /api/users/lookup?email=` — 인증된 active 사용자 누구나 호출 가능. **정확히 일치하는**
active 사용자만 반환한다. rate limit 대상(user 당 20회/분).

`GET /api/users/search?q=` — 이름/이메일 **부분 일치** active 사용자 목록(최소 2자, 상한 20,
자기 자신 제외). 클릭 방식 멤버 선택 UX 용. 사내 서비스라 부분 검색을 허용하되, 결과 상한·
최소 필드(id/email/display_name)·rate limit 으로 이메일 열거를 제한한다.

`PATCH /api/users/me` — 현재 사용자의 표시 이름 편집(프로필). 셋업 admin 이 기본 이름
"Administrator" 를 실명으로 바꾸는 등에 쓴다.

`PUT /api/users/me/password` — 본인 비밀번호 변경. 현재 비밀번호 검증 후 새 비밀번호를
정책 검증·해싱해 갱신하고, 도용 세션 차단을 위해 전체 refresh 세션을 폐기한다.

`POST/DELETE /api/users/me/avatar` — 본인 프로필 아바타 업로드/삭제(MinIO 저장).
`GET /api/users/{user_id}/avatar` — 아바타 조회(인증 필요, 게이트웨이 스트리밍). 사내
서비스라 로그인 사용자는 누구든 다른 사용자의 아바타를 볼 수 있다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import File as FileParam

from app.api.deps import (
    CurrentUser,
    DbSession,
    RedisClient,
    rate_limit_ip,
    rate_limit_user,
)
from app.api.download import content_disposition
from app.core.security import hash_password
from app.models import User
from app.schemas.auth import UserResponse
from app.schemas.users import (
    AvatarResponse,
    MeUpdateRequest,
    PasswordChangeRequest,
    UserLookupResponse,
)
from app.services.avatars import (
    AvatarError,
    clear_avatar,
    prepare_avatar_download,
    set_avatar,
)
from app.services.storage import get_storage
from app.services.tokens import revoke_all_user_tokens
from app.services.users import (
    PasswordChangeError,
    check_password_change,
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


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_ip("password_change", "rate_limit_login_per_min"))],
)
async def change_my_password(
    payload: PasswordChangeRequest,
    current: CurrentUser,
    session: DbSession,
    redis: RedisClient,
) -> Response:
    """본인 비밀번호를 변경한다 (PRD 10장).

    현재 비밀번호 검증(불일치 400) 후 새 비밀번호 정책 검증(위반 422)을 거쳐 password_hash 를
    갱신한다. 도용 세션 차단을 위해 성공 시 해당 사용자의 모든 refresh 세션을 폐기한다
    (현재 access 토큰은 만료까지 유효). 남용 방지로 로그인과 동일한 IP rate limit 을 건다.
    """
    try:
        check_password_change(
            current.password_hash, payload.current_password, payload.new_password
        )
    except PasswordChangeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    current.password_hash = hash_password(payload.new_password)
    await session.commit()
    # 비밀번호 변경 = 전체 refresh 세션 무효화 (도용 세션 차단).
    await revoke_all_user_tokens(redis, current.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/avatar",
    response_model=AvatarResponse,
    dependencies=[Depends(rate_limit_user("upload", "rate_limit_upload_per_min"))],
)
async def upload_my_avatar(
    current: CurrentUser,
    session: DbSession,
    file: Annotated[UploadFile, FileParam(...)],
) -> AvatarResponse:
    """본인 프로필 아바타 업로드 (multipart `file`). PNG/JPEG/WEBP, 최대 2MB.

    프론트가 클라이언트에서 256x256 로 리사이즈해 올리므로 서버측 이미지 처리는 없다.
    MinIO `avatars/{userId}` 에 저장(덮어쓰기)하고 avatar_url 을 조회 API 경로로 갱신한다.
    """
    try:
        avatar_url = await set_avatar(session, get_storage(), current, file)
    except AvatarError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return AvatarResponse(avatar_url=avatar_url)


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_avatar(current: CurrentUser, session: DbSession) -> Response:
    """본인 프로필 아바타 삭제 — MinIO 객체 제거(없어도 무시) + avatar_url=NULL. 멱등."""
    await clear_avatar(session, get_storage(), current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{user_id}/avatar")
async def get_user_avatar(
    user_id: int, _current: CurrentUser, session: DbSession
) -> Response:
    """아바타 조회 (인증 필요, 게이트웨이 스트리밍). 없으면 404.

    파일 다운로드와 동일하게 presigned URL 을 노출하지 않고 X-Accel-Redirect 로 nginx→MinIO
    스트리밍을 유도한다. content-type 은 저장 시 타입을 보존하고, 사설 캐시를 1시간 허용한다.
    사내 서비스라 로그인 사용자는 누구의 아바타든 조회할 수 있다.
    """
    try:
        internal, mime = await prepare_avatar_download(session, get_storage(), user_id)
    except AvatarError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "X-Accel-Redirect": internal,
            "Content-Type": mime,
            "Content-Disposition": content_disposition(
                f"avatar-{user_id}", inline=True
            ),
            # 아바타는 자주 바뀌지 않으므로 사설 캐시 1시간 허용(URL 의 ?v= 로 갱신 시 무효화).
            "Cache-Control": "private, max-age=3600",
        },
    )


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
