"""파일 미리보기 분류/준비 서비스 (PRD 3.2 — 텍스트/이미지/PDF 미리보기).

미리보기는 다운로드와 달리 브라우저가 **인라인 렌더**하도록 유도한다. 지원 타입별 전략:

  - image/*, application/pdf → 게이트웨이 모델(X-Accel-Redirect)로 nginx→MinIO 인라인 스트리밍.
    브라우저가 네이티브로 렌더하며 backend 는 바이트를 적재하지 않는다(대용량 안전).
  - 텍스트 계열(text/*, application/json, csv, xml, javascript 등) → backend 가 앞부분 최대 1MiB 만
    범위 요청으로 읽어 인라인 본문으로 반환한다. 1MiB 초과 원본은 앞부분만 잘라 주고
    `truncated=True` 로 표시한다(전체 적재 방지 + 사용 가능한 head 미리보기 제공).
  - video/mp4 → 바이트가 아니라 **스트림 주소**를 계획한다. 영상은 처음부터 끝까지 받아야
    재생되는 게 아니라 `<video>` 가 Range 로 필요한 구간만 집어 오므로, 미리보기 라우트는
    단기 티켓이 붙은 무헤더 GET 주소만 돌려주고 실제 바이트는 그 스트림 라우트가 매 요청
    게이트웨이(X-Accel-Redirect)로 흘린다. 10GB 짜리도 즉시 재생·구간 이동이 된다.
  - 그 외(zip, office 바이너리, mp4 아닌 video/audio 등) → 미지원. 라우트가 415 로 응답하며
    프론트는 다운로드로 폴백한다.

판정은 저장된 MIME 을 먼저 믿되, 그것이 **일반형**(application/octet-stream 등)일 때는
파일명 확장자로 되짚는다. 업로드는 브라우저가 준 Content-Type 을 그대로 저장하는데(files.py
upload_file), 브라우저·OS 에 확장자 매핑이 없으면 .md 든 .csv 든 전부 octet-stream 으로
올라온다 — 그대로 두면 텍스트 파일이 "미리보기 미지원"으로 떨어진다. 저장된 MIME 을 고치는
대신 판정만 되짚는 이유는, 이미 올라간 파일까지 재업로드 없이 함께 고쳐지기 때문이다.

접근 검증(파일 소유/그룹 권한, 공유 유효성)은 각 호출자(파일 라우트/공유 라우트)가 담당하고,
이 모듈은 검증이 끝난 File 로 "무엇을 어떻게 렌더할지"만 계획한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
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

# 인라인 재생을 지원하는 영상 MIME. 브라우저 호환이 사실상 보장되는 mp4 하나만 연다.
VIDEO_MIMES = {"video/mp4"}

# "형식을 모르겠다"는 뜻의 MIME. 이때만 확장자로 되짚는다 — 브라우저가 형식을 제대로
# 알려줬다면 그쪽이 파일명보다 믿을 만하다(확장자는 언제든 틀리게 붙일 수 있다).
_GENERIC_MIMES = {
    "application/octet-stream",
    "binary/octet-stream",
    "application/x-download",
    "unknown/unknown",
}

PreviewKind = Literal["image", "pdf", "text", "video", "unsupported"]

# 텍스트 미리보기의 하위 종류 — 프런트가 서식 렌더러를 붙일지 정하는 데 쓴다.
TextKind = Literal["markdown", "plain"]

# 확장자 → 미리보기 종류. 사내에서 실제로 오가는 것들만 담는다.
_EXT_KINDS: dict[str, PreviewKind] = {
    # 문서·설정·로그·소스 — 전부 앞부분을 그대로 읽어 보여준다.
    **{
        ext: "text"
        for ext in (
            "md", "markdown", "txt", "text", "log", "csv", "tsv", "json", "jsonl", "ndjson",
            "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "properties", "xml", "sql",
            "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "sh", "bash", "zsh", "ps1", "bat",
            "css", "scss", "less", "html", "htm", "rs", "go", "java", "kt", "c", "h", "cpp",
            "hpp", "cs", "rb", "php", "swift", "r", "lua", "pl", "dockerfile", "gitignore",
            "srt", "vtt", "diff", "patch",
        )
    },
    "pdf": "pdf",
    **{ext: "image" for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "ico")},
    # svg 는 텍스트지만 스크립트 위험이 있어 렌더 대신 원문으로 — MIME 경로와 같은 규칙이다.
    "svg": "text",
    "mp4": "video",
}

# 서식대로 그려 주는 텍스트 확장자. 나머지 텍스트는 원문 그대로 보여준다.
_MARKDOWN_EXTS = {"md", "markdown", "mdown", "mkd"}


def _extension(filename: str | None) -> str:
    """파일명에서 소문자 확장자만 뽑는다(점 없음). 확장자가 없으면 빈 문자열."""
    if not filename:
        return ""
    return PurePosixPath(filename).suffix.lstrip(".").lower()


def classify(mime_type: str | None, filename: str | None = None) -> PreviewKind:
    """MIME 으로 미리보기 종류를 판정하고, 판정이 안 되면 확장자로 되짚는다.

    확장자는 **보조 수단**이다. 쓸 만한 MIME 이 있으면 그쪽을 따르고, MIME 이 없거나
    "형식 모름"(application/octet-stream 등)일 때만 파일명을 본다.
    """
    mime = (mime_type or "").split(";", 1)[0].strip().lower()

    if mime and mime not in _GENERIC_MIMES:
        if mime == "image/svg+xml":
            return "text"
        if mime.startswith("image/"):
            return "image"
        if mime == "application/pdf":
            return "pdf"
        if mime in VIDEO_MIMES:
            return "video"
        if mime.startswith("text/") or mime in _TEXT_LIKE_MIMES:
            return "text"
        # 알려진 MIME 인데 지원 목록에 없다 — 확장자로 뒤집지 않는다(zip·office 등).
        return "unsupported"

    return _EXT_KINDS.get(_extension(filename), "unsupported")


def text_kind(mime_type: str | None, filename: str | None = None) -> TextKind:
    """텍스트 미리보기를 서식대로 그릴지(markdown) 원문으로 둘지(plain).

    마크다운 판정은 확장자로만 한다. `text/markdown` 을 실어 보내는 브라우저는 드물고,
    반대로 확장자가 .md 인데 text/plain 으로 오는 경우가 대부분이라 MIME 은 신호가 못 된다.
    """
    if _extension(filename) in _MARKDOWN_EXTS:
        return "markdown"
    if (mime_type or "").split(";", 1)[0].strip().lower() == "text/markdown":
        return "markdown"
    return "plain"


@dataclass(frozen=True)
class PreviewPlan:
    """미리보기 렌더 계획. 라우트가 이 계획으로 응답을 만든다.

    - kind="redirect": 게이트웨이 인라인 스트리밍(internal_redirect, mime).
    - kind="text": backend 인라인 본문(text_content, truncated). mime 은 text/plain 계열.
    - kind="stream": 바이트 대신 스트림 주소를 준다(영상). 라우트가 티켓을 발급해 JSON 포인터로
      응답하고, 브라우저가 그 주소로 Range 요청을 보낸다.
    - kind="unsupported": 미지원(415). mime 은 원본 MIME(프론트 폴백 판단용).
    """

    kind: Literal["redirect", "text", "stream", "unsupported"]
    mime: str
    internal_redirect: str | None = None
    text_content: bytes | None = None
    truncated: bool = False
    #: text 일 때만 의미가 있다. 프런트가 마크다운 렌더러를 붙일지 판단한다.
    text_kind: TextKind = "plain"


async def build_preview_plan(storage: StorageService, file: File) -> PreviewPlan:
    """접근 검증이 끝난 File 로 미리보기 계획을 만든다. 폴더/삭제 여부는 호출자가 먼저 배제한다."""
    kind = classify(file.mime_type, file.name)
    original_mime = file.mime_type or "application/octet-stream"

    if kind == "unsupported":
        return PreviewPlan(kind="unsupported", mime=original_mime)

    if kind == "video":
        # presign 하지 않는다 — 재생은 Range 요청 여러 건이라 그때마다 스트림 라우트가 새로 만든다.
        return PreviewPlan(kind="stream", mime=original_mime)

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
        text_kind=text_kind(file.mime_type, file.name),
    )
