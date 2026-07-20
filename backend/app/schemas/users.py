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
