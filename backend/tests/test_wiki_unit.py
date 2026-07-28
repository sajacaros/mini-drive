"""위키 인덱싱 대상 판정 단위 테스트 (DB/Redis 불필요) — spec/wiki-index.md.

`indexable` 은 "토글을 켤 수 있는가"를 정하고, 대상이 아니면 **사용자에게 보여줄 이유**를
함께 돌려준다. 켰는데 조용히 아무 일도 일어나지 않는 상태를 막기 위한 것이라, 이유 문구가
비어 있지 않은지도 함께 본다.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.models import File
from app.services.wiki import INDEXABLE_EXTENSIONS, indexable


def _file(
    name: str, *, size: int = 1024, is_folder: bool = False, is_deleted: bool = False
) -> File:
    f = File()
    f.id = 1
    f.name = name
    f.size = size
    f.is_folder = is_folder
    f.is_deleted = is_deleted
    return f


class TestSupportedFormats:
    def test_markdown_and_html_are_indexable(self) -> None:
        for name in ("guide.md", "guide.markdown", "page.html", "page.htm"):
            assert indexable(_file(name)).ok, name

    def test_extension_match_is_case_insensitive(self) -> None:
        # 업로드 파일명은 대소문자가 섞여 온다 — 확장자 비교에서 걸러지면 안 된다.
        for name in ("README.MD", "Chapter.Html"):
            assert indexable(_file(name)).ok, name

    def test_unsupported_formats_rejected_with_reason(self) -> None:
        # v1 이 md/html 로 한정된 근거는 실측이다 — 사내 PDF 43% 가 추출 텍스트 0.
        for name in ("plan.pdf", "deck.pptx", "spec.docx", "photo.png", "noext"):
            verdict = indexable(_file(name))
            assert not verdict.ok, name
            assert verdict.reason, name

    def test_extensions_constant_matches_behavior(self) -> None:
        for ext in INDEXABLE_EXTENSIONS:
            assert indexable(_file(f"doc{ext}")).ok, ext


class TestNonFileTargets:
    def test_folder_itself_is_not_a_target(self) -> None:
        # 폴더 토글은 하위 파일을 대상으로 삼는다 — 폴더 자체가 인덱싱되지는 않는다.
        verdict = indexable(_file("문서함", is_folder=True))
        assert not verdict.ok and verdict.reason

    def test_trashed_file_rejected(self) -> None:
        verdict = indexable(_file("guide.md", is_deleted=True))
        assert not verdict.ok and verdict.reason


class TestSizeLimit:
    def test_at_limit_is_allowed(self) -> None:
        limit = get_settings().wiki_max_input_bytes
        assert indexable(_file("guide.md", size=limit)).ok

    def test_over_limit_rejected(self) -> None:
        limit = get_settings().wiki_max_input_bytes
        verdict = indexable(_file("guide.md", size=limit + 1))
        assert not verdict.ok and verdict.reason
