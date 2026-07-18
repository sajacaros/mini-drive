"""파일 미리보기 분류/준비 서비스 (PRD 3.2 — 텍스트/이미지/PDF 미리보기).

미리보기는 다운로드와 달리 브라우저가 **인라인 렌더**하도록 유도한다. 지원 타입별 전략:

  - image/*, application/pdf → 게이트웨이 모델(X-Accel-Redirect)로 nginx→MinIO 인라인 스트리밍.
    브라우저가 네이티브로 렌더하며 backend 는 바이트를 적재하지 않는다(대용량 안전).
  - 텍스트 계열(text/*, application/json, csv, xml, javascript 등) → backend 가 앞부분 최대 1MiB 만
    범위 요청으로 읽어 인라인 본문으로 반환한다. 1MiB 초과 원본은 앞부분만 잘라 주고
    `truncated=True` 로 표시한다(전체 적재 방지 + 사용 가능한 head 미리보기 제공).
  - 그 외(zip, office 바이너리, video/audio 등) → 미지원. 라우트가 415 로 응답하며 프론트는
    다운로드로 폴백한다.

접근 검증(파일 소유/그룹 권한, 공유 유효성)은 각 호출자(파일 라우트/공유 라우트)가 담당하고,
이 모듈은 검증이 끝난 File 로 "무엇을 어떻게 렌더할지"만 계획한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import File
from app.services.storage import StorageService

# 텍스트 미리보기 앞부분 상한(1 MiB). 초과 원본은 여기까지만 읽어 truncated 로 표시한다.
PREVIEW_TEXT_MAX_BYTES = 1024 * 1024

# 텍스트로 취급할 비-text/* MIME 화이트리스트(구조화 텍스트 계열).
_TEXT_LIKE_MIMES = {
    "application/json",
    "application/ld+json",
    "application/x-ndjson",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/ecmascript",
    "application/csv",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/sql",
    "image/svg+xml",  # SVG 는 텍스트지만 스크립트 위험이 있어 렌더 대신 원문 텍스트로 취급.
}

PreviewKind = Literal["image", "pdf", "text", "unsupported"]


def classify(mime_type: str | None) -> PreviewKind:
    """MIME 으로 미리보기 종류를 판정한다."""
    if not mime_type:
        return "unsupported"
    mime = mime_type.split(";", 1)[0].strip().lower()
    if mime == "image/svg+xml":
        return "text"
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or mime in _TEXT_LIKE_MIMES:
        return "text"
    return "unsupported"


@dataclass(frozen=True)
class PreviewPlan:
    """미리보기 렌더 계획. 라우트가 이 계획으로 응답을 만든다.

    - kind="redirect": 게이트웨이 인라인 스트리밍(internal_redirect, mime).
    - kind="text": backend 인라인 본문(text_content, truncated). mime 은 text/plain 계열.
    - kind="unsupported": 미지원(415). mime 은 원본 MIME(프론트 폴백 판단용).
    """

    kind: Literal["redirect", "text", "unsupported"]
    mime: str
    internal_redirect: str | None = None
    text_content: bytes | None = None
    truncated: bool = False


async def build_preview_plan(storage: StorageService, file: File) -> PreviewPlan:
    """접근 검증이 끝난 File 로 미리보기 계획을 만든다. 폴더/삭제 여부는 호출자가 먼저 배제한다."""
    kind = classify(file.mime_type)
    original_mime = file.mime_type or "application/octet-stream"

    if kind == "unsupported":
        return PreviewPlan(kind="unsupported", mime=original_mime)

    if kind in ("image", "pdf"):
        presigned = await storage.presign_get_async(file.file_key)
        internal = storage.to_internal_redirect(presigned)
        return PreviewPlan(kind="redirect", mime=original_mime, internal_redirect=internal)

    # 텍스트: 앞부분만 읽어 인라인 본문으로.
    truncated = file.size > PREVIEW_TEXT_MAX_BYTES
    read_len = PREVIEW_TEXT_MAX_BYTES if truncated else file.size
    content = await storage.get_head_bytes_async(file.file_key, read_len)
    return PreviewPlan(
        kind="text",
        mime="text/plain; charset=utf-8",
        text_content=content,
        truncated=truncated,
    )
