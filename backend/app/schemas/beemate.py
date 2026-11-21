"""BeeMate API 요청·응답 스키마 (spec/beemate.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Platform = Literal["android", "ios", "web"]

# 클라이언트가 만든 고유 id — UUID 등. URL 쿼리에도 실리므로 안전한 문자만 받는다.
CLIENT_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("기기 이름을 입력하세요.")
    return value[:100]


class DeviceRegisterRequest(BaseModel):
    """기기 자동 등록(upsert). 이미 있으면 그대로 돌려주고 이름은 바꾸지 않는다."""

    client_id: str = Field(pattern=CLIENT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    platform: Platform

    _strip = field_validator("name")(_clean_name)


class DeviceUpdateRequest(BaseModel):
    """부분 수정 — 준 필드만 바꾼다."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_favorite: bool | None = None
    is_hidden: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)

    @model_validator(mode="after")
    def _not_empty(self) -> DeviceUpdateRequest:
        if self.name is None and self.is_favorite is None and self.is_hidden is None:
            raise ValueError("바꿀 항목이 없습니다.")
        return self


class DeviceResponse(BaseModel):
    id: int
    name: str
    platform: str
    is_favorite: bool
    is_hidden: bool
    created_at: datetime
    last_seen_at: datetime | None
    # 이벤트 스트림이 지금 붙어 있는가 (Redis 키, 75초 TTL).
    online: bool = False


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    to_device_id: int
    from_device_id: int | None
    name: str
    mime_type: str
    size: int
    created_at: datetime
    # 만료 시각 — 받는 쪽이 "곧 사라짐"을 보여 줄 수 있게 서버가 계산해 준다.
    expires_at: datetime
