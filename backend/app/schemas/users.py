"""일반 사용자 조회 API 스키마 (그룹 초대 UX)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UserLookupResponse(BaseModel):
    """이메일 정확 일치 조회 결과 (그룹 초대용). 민감 정보는 노출하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
