"""HTML → Markdown 변환 (spec/wiki-index.md).

인덱싱 파이프라인은 `입력 → Markdown → PageIndex md_to_tree` 로 고정돼 있다. HTML 을 md 로
바꾸는 이 단계가 그 앞단이고, 나중에 다른 형식(pdf·docx·pptx)을 지원할 때도 변환기만 추가하면
인덱싱 쪽은 바뀌지 않는다.

**표준 라이브러리만 쓴다.** 이 변환이 필요로 하는 것은 문서 구조(제목 계층)와 본문 텍스트뿐이고,
`h1 → #` 매핑은 결정적이라 테스트로 고정할 수 있다. 저장소가 런타임 의존성을 최소로 유지해 온
것과도 맞다(LLM 패키지 제거 이력).

완벽한 마크다운 왕복 변환이 목적이 아니다 — 트리 생성기가 읽을 **제목 계층과 문단**이 살아
있으면 된다. 스타일·스크립트·내비게이션은 인덱싱에 잡음이라 버린다.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# 내용을 통째로 버리는 요소 — 화면 장식과 스크립트는 문서 내용이 아니다.
_DROP_ELEMENTS = frozenset({"script", "style", "head", "noscript", "svg", "template"})
# 문서 본문이 아닌 껍데기. 사내 HTML 은 <main>/<section> 구조가 흔해 안전하게 버릴 수 있다.
_CHROME_ELEMENTS = frozenset({"nav", "footer", "aside"})
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCK_ELEMENTS = frozenset(
    {
        "p", "div", "section", "article", "header", "main", "blockquote",
        "ul", "ol", "li", "table", "tr", "hr", "pre",
        *_HEADINGS,
    }
)

_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


class _MarkdownExtractor(HTMLParser):
    """HTML 이벤트를 마크다운 조각으로 옮긴다.

    인라인 텍스트는 버퍼에 모으고 블록 경계에서만 개행을 넣는다 — 태그 사이 줄바꿈이 그대로
    본문에 섞이면 트리 생성기가 문단을 잘못 자른다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title: str | None = None
        self._drop_depth = 0
        self._in_title = False
        self._in_pre = False
        self._list_stack: list[str] = []
        self._ordered_index: list[int] = []
        self._pending_prefix: str | None = None
        self._line: list[str] = []

    # --- 내부 버퍼 ---------------------------------------------------------

    def _flush_line(self) -> None:
        text = "".join(self._line)
        self._line.clear()
        if not self._in_pre:
            text = re.sub(r"[ \t\r\n]+", " ", text).strip()
        if not text:
            self._pending_prefix = None
            return
        prefix = self._pending_prefix or ""
        self._pending_prefix = None
        self.out.append(f"{prefix}{text}\n\n")

    def _emit_raw(self, chunk: str) -> None:
        self._flush_line()
        self.out.append(chunk)

    # --- HTMLParser 훅 -----------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_ELEMENTS or tag in _CHROME_ELEMENTS:
            # <title> 은 head 안에 있지만 문서 제목으로 쓰므로 별도로 잡는다.
            self._drop_depth += 1
            return
        if self._drop_depth and tag != "title":
            return

        if tag == "title":
            self._in_title = True
            return
        if tag == "br":
            self._line.append(" ")
            return
        if tag == "pre":
            self._flush_line()
            self._in_pre = True
            self.out.append("```\n")
            return
        if tag in _HEADINGS:
            self._flush_line()
            self._pending_prefix = "#" * _HEADINGS[tag] + " "
            return
        if tag in ("ul", "ol"):
            self._flush_line()
            self._list_stack.append(tag)
            self._ordered_index.append(0)
            return
        if tag == "li":
            self._flush_line()
            depth = max(len(self._list_stack) - 1, 0)
            indent = "  " * depth
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ordered_index[-1] += 1
                self._pending_prefix = f"{indent}{self._ordered_index[-1]}. "
            else:
                self._pending_prefix = f"{indent}- "
            return
        if tag == "hr":
            self._emit_raw("---\n\n")
            return
        if tag in _BLOCK_ELEMENTS:
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_ELEMENTS or tag in _CHROME_ELEMENTS:
            self._drop_depth = max(self._drop_depth - 1, 0)
            return
        if tag == "title":
            self._in_title = False
            return
        if self._drop_depth:
            return

        if tag == "pre":
            text = "".join(self._line).strip("\n")
            self._line.clear()
            self._in_pre = False
            self.out.append(f"{text}\n```\n\n" if text else "```\n\n")
            return
        if tag in ("ul", "ol"):
            self._flush_line()
            if self._list_stack:
                self._list_stack.pop()
                self._ordered_index.pop()
            return
        if tag in _BLOCK_ELEMENTS:
            self._flush_line()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data
            return
        if self._drop_depth:
            return
        self._line.append(data)

    def close(self) -> None:
        super().close()
        self._flush_line()


def html_to_markdown(html: str) -> str:
    """HTML 문서를 인덱싱용 Markdown 으로 바꾼다.

    `<title>` 은 문서에 `h1` 이 없을 때만 최상위 제목으로 얹는다 — 트리에 뿌리가 하나 있어야
    하위 절이 매달린다. h1 이 이미 있으면 제목이 중복되므로 넣지 않는다.
    """
    parser = _MarkdownExtractor()
    parser.feed(html)
    parser.close()

    body = "".join(parser.out)
    body = _TRAILING_SPACE.sub("\n", body)
    body = _MULTI_BLANK.sub("\n\n", body).strip()

    title = (parser.title or "").strip()
    if title and not re.search(r"^# ", body, re.M):
        body = f"# {title}\n\n{body}" if body else f"# {title}"
    return body + "\n" if body else ""


__all__ = ["html_to_markdown"]
