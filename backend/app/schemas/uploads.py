"""재개 가능 업로드 API 요청·응답 스키마 (PRD 3.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ResumableInitRequest(BaseModel):
    """새 파일 재개 업로드 세션 개시 (POST /api/files/uploads)."""

    filename: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    total_size: int = Field(gt=0, description="업로드할 파일 전체 바이트 수")
    mime_type: str | None = Field(default=None, max_length=100)


class ResumableReuploadInitRequest(BaseModel):
    """기존 파일 재업로드(새 버전) 재개 세션 개시 (POST /api/files/{id}/uploads)."""

    total_size: int = Field(gt=0)
    mime_type: str | None = Field(default=None, max_length=100)
    base_version: int | None = Field(
        default=None, description="충돌 감지 기준 버전(생략 시 강제 덮어쓰기)"
    )


class ResumableSessionResponse(BaseModel):
    """세션 상태 — 개시 응답이자 재개(GET) 응답. 클라이언트는 이 값으로 청크를 나누고 이어올린다."""

    session_id: str
    kind: Literal["new", "version"]
    file_id: int
    part_size: int
    total_parts: int
    total_size: int
    uploaded_parts: list[int]
    received_bytes: int
    expires_at: datetime


class ResumablePartResponse(BaseModel):
    """단일 파트 업로드 결과."""

    part_number: int
    size: int
    etag: str
