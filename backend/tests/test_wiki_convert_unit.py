"""HTML → Markdown 변환 단위 테스트 (의존성·DB 불필요) — spec/wiki-index.md.

이 변환의 목적은 완벽한 왕복이 아니라 **트리 생성기가 읽을 제목 계층과 문단을 살리는 것**이다.
그래서 검증도 거기에 맞춘다 — 제목 레벨이 보존되는가, 잡음(스타일·스크립트)이 걷히는가,
문장이 태그 사이 줄바꿈 때문에 잘리지 않는가.
"""

from __future__ import annotations

from app.services.wiki_convert import html_to_markdown


class TestHeadings:
    def test_heading_levels_map_to_hashes(self) -> None:
        html = "".join(f"<h{i}>제목{i}</h{i}>" for i in range(1, 7))
        md = html_to_markdown(html)
        for i in range(1, 7):
            assert f"{'#' * i} 제목{i}" in md

    def test_title_becomes_h1_when_absent(self) -> None:
        # 트리에 뿌리가 하나 있어야 하위 절이 매달린다.
        html = "<html><head><title>문서 제목</title></head><body><h2>절</h2></body></html>"
        md = html_to_markdown(html)
        assert md.startswith("# 문서 제목")
        assert "## 절" in md

    def test_title_not_duplicated_when_h1_exists(self) -> None:
        md = html_to_markdown("<head><title>제목</title></head><body><h1>본문 제목</h1></body>")
        assert md.count("# ") == md.count("# 본문 제목")
        assert "# 제목\n" not in md


class TestNoise:
    def test_style_and_script_dropped(self) -> None:
        # 사내 HTML 은 <style> 블록이 본문보다 큰 경우가 흔하다.
        html = """
        <head><style>body { color: red; --x: 1; }</style></head>
        <body><script>alert('x')</script><p>본문</p></body>
        """
        md = html_to_markdown(html)
        assert "본문" in md
        assert "color" not in md and "alert" not in md

    def test_chrome_elements_dropped(self) -> None:
        html = "<body><nav>메뉴1 메뉴2</nav><p>내용</p><footer>푸터</footer></body>"
        md = html_to_markdown(html)
        assert "내용" in md
        assert "메뉴1" not in md and "푸터" not in md


class TestBlocks:
    def test_paragraphs_separated_by_blank_line(self) -> None:
        md = html_to_markdown("<p>첫째</p><p>둘째</p>")
        assert "첫째\n\n둘째" in md

    def test_sentence_not_split_by_inline_tags(self) -> None:
        # 태그 사이 줄바꿈이 본문에 섞이면 트리 생성기가 문단을 잘못 자른다.
        html = "<p>앞부분\n  <strong>강조</strong>\n  뒷부분</p>"
        md = html_to_markdown(html)
        assert "앞부분 강조 뒷부분" in md

    def test_unordered_list(self) -> None:
        md = html_to_markdown("<ul><li>하나</li><li>둘</li></ul>")
        assert "- 하나" in md and "- 둘" in md

    def test_ordered_list_numbers(self) -> None:
        md = html_to_markdown("<ol><li>첫째</li><li>둘째</li></ol>")
        assert "1. 첫째" in md and "2. 둘째" in md

    def test_nested_list_indented(self) -> None:
        md = html_to_markdown("<ul><li>상위</li><ul><li>하위</li></ul></ul>")
        assert "- 상위" in md and "  - 하위" in md

    def test_pre_becomes_fenced_code(self) -> None:
        md = html_to_markdown("<pre>kubectl get pods\nkubectl logs</pre>")
        assert "```" in md
        assert "kubectl get pods\nkubectl logs" in md


class TestText:
    def test_entities_decoded(self) -> None:
        md = html_to_markdown("<p>a &amp; b &lt;c&gt; &#54620;</p>")
        assert "a & b <c> 한" in md

    def test_empty_document_yields_empty(self) -> None:
        assert html_to_markdown("") == ""
        assert html_to_markdown("<html><body></body></html>") == ""

    def test_no_runaway_blank_lines(self) -> None:
        md = html_to_markdown("<div><div><div><p>내용</p></div></div></div>")
        assert "\n\n\n" not in md

    def test_output_ends_with_single_newline(self) -> None:
        md = html_to_markdown("<p>내용</p>")
        assert md.endswith("내용\n")
