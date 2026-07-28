"""Markdown → 트리 파싱 단위 테스트 (LLM·DB 불필요) — spec/wiki-index.md.

트리 구조는 전부 결정적 파싱이라 LLM 없이 고정할 수 있다. 요약만 LLM 을 쓴다.
검증 축: 헤더 추출(코드펜스 인식) / level 중첩 / 구간 경계(end_line) / 본문 슬라이싱 /
헤더 없는 문서 / 머리말 누락 방지.
"""

from __future__ import annotations

from app.services.wiki_tree import (
    build_tree,
    extract_headings,
    flatten,
    node_text,
    own_text,
    parse,
    to_dict,
)

DOC = """머리말 문장.

# 제목

도입부.

## 절 1

내용 1.

### 소절 1-1

내용 1-1.

## 절 2

내용 2.
"""


class TestExtractHeadings:
    def test_levels_and_line_numbers(self) -> None:
        heads, lines = extract_headings(DOC)
        assert [(h.level, h.title) for h in heads] == [
            (1, "제목"),
            (2, "절 1"),
            (3, "소절 1-1"),
            (2, "절 2"),
        ]
        # line_num 은 1-based — 원문 슬라이싱과 UI 앵커가 그대로 쓴다.
        assert lines[heads[0].line_num - 1] == "# 제목"

    def test_hash_inside_code_fence_ignored(self) -> None:
        # 코드블록의 `#` 는 주석이지 헤더가 아니다.
        md = "# 진짜\n\n```bash\n# 가짜 주석\necho hi\n```\n\n## 진짜2\n"
        heads, _ = extract_headings(md)
        assert [h.title for h in heads] == ["진짜", "진짜2"]

    def test_tilde_fence_also_recognized(self) -> None:
        md = "# 진짜\n\n~~~\n# 가짜\n~~~\n"
        heads, _ = extract_headings(md)
        assert [h.title for h in heads] == ["진짜"]

    def test_hash_without_space_is_not_heading(self) -> None:
        heads, _ = extract_headings("#태그\n# 제목\n")
        assert [h.title for h in heads] == ["제목"]


class TestTreeShape:
    def test_nesting_by_level(self) -> None:
        roots, _ = parse(DOC, doc_name="doc")
        assert len(roots) == 1
        root = roots[0]
        assert root.title == "제목"
        assert [c.title for c in root.children] == ["절 1", "절 2"]
        assert [g.title for g in root.children[0].children] == ["소절 1-1"]

    def test_node_ids_in_document_order(self) -> None:
        roots, _ = parse(DOC, doc_name="doc")
        assert [n.node_id for n in flatten(roots)] == ["0001", "0002", "0003", "0004"]

    def test_higher_level_heading_closes_deeper_one(self) -> None:
        # h3 다음에 h2 가 오면 h3 구간은 거기서 끝난다 — 형제만 경계가 아니다.
        roots, lines = parse(DOC, doc_name="doc")
        sub = roots[0].children[0].children[0]  # 소절 1-1
        assert sub.title == "소절 1-1"
        text = node_text(lines, sub)
        assert "내용 1-1" in text
        assert "절 2" not in text and "내용 2" not in text


class TestTextSlicing:
    def test_own_text_excludes_children(self) -> None:
        # 자식 구간까지 넣으면 상위 노드 요약이 문서 전체 요약이 되어 노드 구분이 사라진다.
        roots, lines = parse(DOC, doc_name="doc")
        section1 = roots[0].children[0]
        own = own_text(lines, section1)
        assert "내용 1." in own
        assert "소절 1-1" not in own

    def test_node_text_includes_children(self) -> None:
        roots, lines = parse(DOC, doc_name="doc")
        section1 = roots[0].children[0]
        full = node_text(lines, section1)
        assert "내용 1." in full and "내용 1-1." in full

    def test_preamble_before_first_heading_is_covered(self) -> None:
        # 첫 헤더 앞 머리말이 어느 노드에도 안 잡히면 문서 앞부분이 검색에서 통째로 빠진다.
        roots, lines = parse(DOC, doc_name="doc")
        assert roots[0].line_num == 1
        assert "머리말 문장." in node_text(lines, roots[0])


class TestEdgeCases:
    def test_document_without_headings_becomes_single_root(self) -> None:
        roots, lines = parse("본문만 있는 문서.\n두 번째 줄.\n", doc_name="메모.md")
        assert len(roots) == 1
        assert roots[0].title == "메모.md"
        assert "두 번째 줄." in node_text(lines, roots[0])

    def test_serialization_shape(self) -> None:
        roots, lines = parse(DOC, doc_name="doc")
        roots[0].summary = "요약문"
        out = to_dict(roots, doc_name="doc", line_count=len(lines))
        assert out["doc_name"] == "doc" and out["line_count"] == len(lines)
        node = out["structure"][0]
        assert node["node_id"] == "0001" and node["title"] == "제목"
        assert node["summary"] == "요약문"
        assert "nodes" in node
        # 본문(text)은 담지 않는다 — 담으면 트리가 원문의 2배가 된다.
        assert "text" not in node

    def test_empty_children_key_omitted(self) -> None:
        roots, lines = parse("# 하나\n\n내용.\n", doc_name="doc")
        out = to_dict(roots, doc_name="doc", line_count=len(lines))
        assert "nodes" not in out["structure"][0]

    def test_build_tree_handles_deeper_first_heading(self) -> None:
        # h2 로 시작하는 문서(h1 없음)도 뿌리를 갖는다.
        heads, lines = extract_headings("## 절\n\n내용\n")
        roots = build_tree(heads, len(lines))
        assert len(roots) == 1 and roots[0].title == "절"
