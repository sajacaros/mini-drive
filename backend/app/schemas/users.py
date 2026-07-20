"""일반 사용자 조회/자기 정보 수정 API 스키마 (그룹 초대 UX, 프로필)."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


class UserLookupResponse(BaseModel):
    """이메일 정확 일치 조회 결과 (그룹 초대용). 민감 정보는 노출하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str


def _validate_display_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("이름을 입력하세요.")
    return value


class MeUpdateRequest(BaseModel):
    """자기 정보 수정 (PATCH /api/users/me). 현재는 표시 이름만 편집한다."""

    display_name: Annotated[
        str, Field(min_length=1, max_length=100), AfterValidator(_validate_display_name)
    ]


class PasswordChangeRequest(BaseModel):
    """본인 비밀번호 변경 (PUT /api/users/me/password).

    new_password 의 상세 정책(영문+숫자+특수문자)은 라우트에서 validate_password_policy 로
    검증한다 — 여기서는 길이 상한만 둔다(argon2 입력 폭주 방지, register 요청과 동일 규약).
    """

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AvatarResponse(BaseModel):
    """아바타 업로드 결과 (POST /api/users/me/avatar). 저장된 조회 API 경로를 돌려준다."""

    avatar_url: str
