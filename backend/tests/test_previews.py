"""미리보기 분류 유닛 테스트 (app/services/previews.py).

여기서 지키려는 성질은 두 가지다.

1. **저장된 MIME 이 쓸 만하면 그것이 이긴다.** 확장자는 언제든 틀리게 붙을 수 있으므로
   보조 수단이어야 한다.
2. **MIME 이 "형식 모름"이면 확장자로 되짚는다.** 업로드는 브라우저가 준 Content-Type 을
   그대로 저장하는데, 확장자 매핑이 없는 OS·브라우저에서는 .md 나 .csv 가 통째로
   application/octet-stream 으로 올라온다. 되짚지 않으면 평범한 텍스트 문서가
   "미리보기 미지원"으로 떨어진다 — 실제로 그렇게 떨어지고 있었다.
"""

from __future__ import annotations

import pytest

from app.services.previews import classify, text_kind

# --- MIME 이 명확할 때: MIME 을 따른다 -------------------------------------


@pytest.mark.parametrize(
    ("mime", "filename", "expected"),
    [
        ("image/png", "a.png", "image"),
        ("image/jpeg", "photo.jpg", "image"),
        ("application/pdf", "doc.pdf", "pdf"),
        ("text/plain", "notes.txt", "text"),
        ("text/markdown", "README.md", "text"),
        ("application/json", "data.json", "text"),
        ("video/mp4", "clip.mp4", "video"),
        # charset 파라미터가 붙어도 같은 판정.
        ("text/plain; charset=utf-8", "notes.txt", "text"),
        # 대문자 MIME 도 정규화된다.
        ("IMAGE/PNG", "a.png", "image"),
    ],
)
def test_classify_follows_known_mime(mime: str, filename: str, expected: str) -> None:
    assert classify(mime, filename) == expected


def test_svg_is_text_not_image() -> None:
    """SVG 는 스크립트를 품을 수 있어 렌더하지 않고 원문 텍스트로 보여준다."""
    assert classify("image/svg+xml", "logo.svg") == "text"
    # 확장자 경로로 들어와도 같은 규칙이어야 한다.
    assert classify("application/octet-stream", "logo.svg") == "text"


@pytest.mark.parametrize(
    ("mime", "filename"),
    [
        ("application/zip", "bundle.zip"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx"),
        ("video/quicktime", "clip.mov"),
    ],
)
def test_known_but_unsupported_mime_is_not_rescued_by_extension(mime: str, filename: str) -> None:
    """알려진 MIME 인데 지원 목록에 없으면 확장자로 뒤집지 않는다."""
    assert classify(mime, filename) == "unsupported"


def test_known_mime_beats_misleading_extension() -> None:
    """확장자가 거짓이어도 제대로 된 MIME 이 있으면 그쪽을 믿는다."""
    assert classify("image/png", "actually-an-image.txt") == "image"


# --- MIME 이 일반형/비어 있을 때: 확장자로 되짚는다 -------------------------


@pytest.mark.parametrize("generic", ["application/octet-stream", "binary/octet-stream", "", None])
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("README.md", "text"),
        ("CHANGELOG.markdown", "text"),
        ("notes.txt", "text"),
        ("server.log", "text"),
        ("rows.csv", "text"),
        ("config.yaml", "text"),
        ("main.py", "text"),
        ("report.pdf", "pdf"),
        ("photo.JPG", "image"),  # 확장자 대소문자 무시
        ("clip.mp4", "video"),
    ],
)
def test_generic_mime_falls_back_to_extension(
    generic: str | None, filename: str, expected: str
) -> None:
    assert classify(generic, filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "archive.zip",
        "sheet.xlsx",
        "binary.bin",
        "no-extension",
        "",
        None,
    ],
)
def test_generic_mime_with_unknown_extension_stays_unsupported(filename: str | None) -> None:
    assert classify("application/octet-stream", filename) == "unsupported"


def test_missing_mime_and_missing_filename_is_unsupported() -> None:
    assert classify(None, None) == "unsupported"


# --- 마크다운 하위 판정 -----------------------------------------------------


@pytest.mark.parametrize("filename", ["README.md", "a.MD", "doc.markdown", "notes.mkd"])
def test_markdown_detected_by_extension(filename: str) -> None:
    """.md 는 text/plain 으로 올라오는 일이 흔해 확장자가 유일하게 믿을 신호다."""
    assert text_kind("text/plain", filename) == "markdown"


def test_markdown_detected_by_mime_without_extension() -> None:
    assert text_kind("text/markdown", "notes") == "markdown"


@pytest.mark.parametrize("filename", ["notes.txt", "rows.csv", "main.py", "no-extension"])
def test_other_text_stays_plain(filename: str) -> None:
    assert text_kind("text/plain", filename) == "plain"
