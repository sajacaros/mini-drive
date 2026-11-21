"""BeeMate API 요청·응답 스키마 (spec/beemate.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Platform = Literal["android", "ios", "web"]


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("기기 이름을 입력하세요.")
    return value


class DeviceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform: Platform

    _strip = field_validator("name")(_clean_name)


class DeviceUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    _strip = field_validator("name")(_clean_name)


class DeviceResponse(BaseModel):
    id: int
    name: str
    platform: str
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
