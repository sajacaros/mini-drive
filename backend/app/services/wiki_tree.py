"""Markdown → 트리 구조 (spec/wiki-index.md).

[PageIndex](https://github.com/VectifyAI/PageIndex) 의 방법을 따르되 직접 구현한다. md 경로에서
PageIndex 가 하는 일은 ① 헤더 정규식 추출 ② level 로 중첩 ③ 노드별 요약인데, ①②는 결정적
파싱이고 LLM 은 ③에서만 쓴다. 그 셋을 위해 litellm·pymupdf·PyPDF2 를 런타임 의존성으로
들이는 대신(v1 은 PDF 를 쓰지 않는다) 같은 방법을 여기에 둔다. 산출물 형태는 PageIndex 와
맞춰 두어 나중에 PDF 경로를 붙일 때 저장 스키마를 바꾸지 않아도 되게 한다.

산출물:
    {"doc_name": str, "line_count": int,
     "structure": [{"node_id", "title", "line_num", "summary", "nodes": [...]}, ...]}

**노드 본문(text)은 담지 않는다.** 담으면 트리가 원문의 2배가 되어 본문 사본이 Postgres 에
생긴다. 검색은 title+summary 만으로 충분하고(실측 3/3), 선택된 노드의 본문은 질의 시점에
`line_num` 범위로 원문에서 잘라 온다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CODE_FENCE = re.compile(r"^\s*(```|~~~)")

# 이 길이 미만인 절은 요약하지 않고 **본문을 그대로** summary 로 쓴다. 짧은 절은 요약해봐야
# 원문보다 길거나 정보가 준다. PageIndex 의 200토큰 임계값과 같은 취지이고, 토크나이저
# 의존성을 피하려 글자 수로 근사한다(한국어 기준 대략 1토큰 ≈ 1.5~2자).
SHORT_NODE_CHARS = 400


@dataclass
class Heading:
    """마크다운 헤더 한 줄. line_num 은 1-based (원문 슬라이싱과 UI 앵커에 그대로 쓴다)."""

    title: str
    level: int
    line_num: int


@dataclass
class TreeNode:
    title: str
    level: int
    line_num: int
    node_id: str = ""
    end_line: int = 0
    children: list[TreeNode] = field(default_factory=list)
    summary: str | None = None


def extract_headings(markdown: str) -> tuple[list[Heading], list[str]]:
    """마크다운에서 헤더를 뽑는다. 코드블록 안의 `#` 는 주석이므로 건너뛴다."""
    lines = markdown.split("\n")
    out: list[Heading] = []
    in_fence = False
    for idx, line in enumerate(lines, start=1):
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            title = m.group(2).strip()
            if title:
                out.append(Heading(title=title, level=len(m.group(1)), line_num=idx))
    return out, lines


def build_tree(headings: list[Heading], total_lines: int) -> list[TreeNode]:
    """헤더 목록을 level 로 중첩한다.

    각 노드의 `end_line` 은 **자기 하위 트리까지 포함한** 구간의 끝이다 — 즉 같거나 더 얕은
    레벨의 다음 헤더 직전까지다. 단순히 '다음 헤더 직전'으로 잡으면 그 다음 헤더가 자기
    자식일 때 구간이 자식 앞에서 끊겨, 절 전체를 인용해야 하는 자리에서 도입부만 나온다.
    자식을 뺀 자기 본문은 `own_text` 가 따로 계산한다.
    """
    nodes = [
        TreeNode(title=h.title, level=h.level, line_num=h.line_num) for h in headings
    ]
    for i, node in enumerate(nodes):
        node.end_line = total_lines
        for later in nodes[i + 1 :]:
            if later.level <= node.level:
                node.end_line = later.line_num - 1
                break

    roots: list[TreeNode] = []
    stack: list[TreeNode] = []
    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def assign_node_ids(roots: list[TreeNode]) -> None:
    """문서 순서대로 0001 부터 부여한다. 검색 응답의 node_list 가 이 id 를 가리킨다."""
    counter = 0

    def walk(nodes: list[TreeNode]) -> None:
        nonlocal counter
        for n in nodes:
            counter += 1
            n.node_id = str(counter).zfill(4)
            walk(n.children)

    walk(roots)


def node_text(lines: list[str], node: TreeNode) -> str:
    """노드가 덮는 원문 구간 (헤더 줄 포함). line_num 은 1-based."""
    return "\n".join(lines[node.line_num - 1 : node.end_line]).strip()


def own_text(lines: list[str], node: TreeNode) -> str:
    """하위 절을 뺀, 이 노드 자신의 본문. 요약 대상은 이쪽이다.

    자식 구간까지 넣으면 상위 노드의 요약이 문서 전체 요약이 되어 노드 간 구분이 사라진다.
    """
    end = node.children[0].line_num - 1 if node.children else node.end_line
    return "\n".join(lines[node.line_num - 1 : end]).strip()


def flatten(roots: list[TreeNode]) -> list[TreeNode]:
    out: list[TreeNode] = []

    def walk(nodes: list[TreeNode]) -> None:
        for n in nodes:
            out.append(n)
            walk(n.children)

    walk(roots)
    return out


def to_dict(roots: list[TreeNode], *, doc_name: str, line_count: int) -> dict[str, Any]:
    """저장·검색용 직렬화. 본문(text)은 넣지 않는다."""

    def conv(node: TreeNode) -> dict[str, Any]:
        out: dict[str, Any] = {
            "node_id": node.node_id,
            "title": node.title,
            "line_num": node.line_num,
            "end_line": node.end_line,
        }
        if node.summary:
            out["summary"] = node.summary
        if node.children:
            out["nodes"] = [conv(c) for c in node.children]
        return out

    return {
        "doc_name": doc_name,
        "line_count": line_count,
        "structure": [conv(n) for n in roots],
    }


def parse(markdown: str, *, doc_name: str) -> tuple[list[TreeNode], list[str]]:
    """헤더 추출 → 중첩 → id 부여까지의 결정적 단계. 요약은 호출자가 붙인다."""
    headings, lines = extract_headings(markdown)
    if not headings:
        # 헤더가 하나도 없으면 문서 전체를 뿌리 노드 하나로 둔다 — 그래야 검색 대상이 된다.
        root = TreeNode(title=doc_name, level=1, line_num=1, end_line=len(lines))
        roots = [root]
    else:
        roots = build_tree(headings, len(lines))
        # 첫 헤더 앞의 머리말은 어느 노드에도 안 잡힌다. 문서 앞부분이 통째로 검색에서
        # 빠지는 것을 막으려 뿌리가 1번 줄부터 덮게 한다.
        if roots and roots[0].line_num > 1:
            roots[0].line_num = 1
    assign_node_ids(roots)
    return roots, lines


__all__ = [
    "SHORT_NODE_CHARS",
    "Heading",
    "TreeNode",
    "assign_node_ids",
    "build_tree",
    "extract_headings",
    "flatten",
    "node_text",
    "own_text",
    "parse",
    "to_dict",
]
