"""위키 단위 테스트 (DB/Redis/LLM 불필요) — spec/wiki-index.md.

세 덩어리다.

- `indexable` — "토글을 켤 수 있는가"와 안 되는 **이유**. 켰는데 조용히 아무 일도 일어나지
  않는 상태를 막는 것이 목적이라 이유 문구가 비어 있지 않은지도 본다.
- `_catalog_nodes` — JSONB 트리를 화면용 노드로. 파편이 섞여도 안 깨지는지.
- `_keywords`·`_render_catalog` — 질의 프롬프트의 후보 선별과 **문자 예산**. 예산이 없으면
  문서가 늘자마자 모든 질의가 컨텍스트 초과로 죽는다(2026-07-30 실측).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.config import get_settings
from app.models import File
from app.services.wiki import INDEXABLE_EXTENSIONS, _catalog_nodes, indexable
from app.services.wiki_query import _keywords, _render_catalog


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


class TestCatalogNodes:
    """저장된 트리 → 카탈로그 노드 변환 (`_catalog_nodes`).

    트리는 JSONB 라 스키마 보증이 없다. 구 버전 트리나 실패한 인덱싱이 남긴 파편이 섞여도
    화면이 통째로 깨지지 않아야 해서, 좌표가 온전한 노드만 통과시킨다.
    """

    def test_nesting_and_fields_preserved(self) -> None:
        tree = {
            "structure": [
                {
                    "node_id": "0001",
                    "title": "개요",
                    "line_num": 1,
                    "summary": "이 문서는...",
                    "nodes": [
                        {"node_id": "0002", "title": "배경", "line_num": 5, "nodes": []}
                    ],
                }
            ]
        }
        nodes = _catalog_nodes(tree)
        assert len(nodes) == 1
        root = nodes[0]
        assert (root.node_id, root.title, root.line_num) == ("0001", "개요", 1)
        assert root.summary == "이 문서는..."
        assert [c.title for c in root.nodes] == ["배경"]
        # 요약이 없는 절도 정상이다 — 화면이 요약 없이 제목만 그린다.
        assert root.nodes[0].summary is None

    def test_malformed_nodes_are_skipped(self) -> None:
        tree = {
            "structure": [
                {"title": "id 없음", "line_num": 1},
                {"node_id": "0002", "line_num": 2},
                {"node_id": "0003", "title": "줄번호 없음"},
                {"node_id": "0004", "title": "정상", "line_num": 4},
                "문자열",
            ]
        }
        assert [n.node_id for n in _catalog_nodes(tree)] == ["0004"]

    def test_missing_or_broken_tree_is_empty(self) -> None:
        # 인덱싱 전(pending)에는 트리가 없다 — 빈 카탈로그로 내려가 화면이 상태를 설명한다.
        for tree in (None, {}, {"structure": None}, {"structure": "x"}):
            assert _catalog_nodes(tree) == []


class TestKeywords:
    """질문 → 후보 점수용 키워드 (`_keywords`).

    형태소 분석기를 붙이지 않고 조사 사전으로 근사한다 — 이 단계는 후보 **순위**이고 최종
    판단은 LLM 이 하므로, 놓친 키워드의 대가가 오답이 아니라 순위 저하다.
    """

    def test_particles_are_stripped(self) -> None:
        assert _keywords("배포 절차는 어떻게 되나요?") == ["배포", "절차"]

    def test_short_stems_keep_their_particle(self) -> None:
        # 조사를 떼서 1자가 되면 아무 문서에나 걸린다 — 원형을 유지한다.
        assert "키가" in _keywords("키가 무엇인가")

    def test_stopwords_and_single_chars_dropped(self) -> None:
        assert _keywords("그 내용에 대해 알려줘") == []

    def test_mixed_script_and_case(self) -> None:
        assert _keywords("Redis 캐시 TTL 은?") == ["redis", "캐시", "ttl"]


class TestCatalogBudget:
    """노드 선택 프롬프트의 후보 선별과 예산 (`_render_catalog`).

    이 클래스가 막는 회귀는 하나다 — 대상 트리를 전부 프롬프트에 넣어 컨텍스트를 넘기고
    **모든 질의가 400 으로 죽는** 상태(실측 932,753자). 예산은 협상 대상이 아니다.
    """

    @staticmethod
    def _docs(count: int, nodes_per_doc: int = 20) -> list[tuple[object, object]]:
        out = []
        for d in range(count):
            doc = SimpleNamespace(
                tree={
                    "structure": [
                        {
                            "node_id": f"{n:04d}",
                            "title": f"문서{d} 절{n}",
                            "line_num": n + 1,
                            "summary": "가" * 200,
                        }
                        for n in range(nodes_per_doc)
                    ]
                }
            )
            out.append((doc, SimpleNamespace(id=d + 1, name=f"문서{d}.md")))
        return out

    def test_budget_is_never_exceeded(self) -> None:
        catalog, examined, dropped = _render_catalog(self._docs(500), [], budget=60_000)
        assert len(catalog) <= 60_000, len(catalog)
        assert dropped > 0, "500개 문서가 예산 안에 다 들어갔다면 예산이 무의미하다"
        assert examined > 0, "예산 안에 아무것도 안 들어갔다"

    def test_relevant_nodes_win_the_budget(self) -> None:
        docs = self._docs(50)
        # 마지막 문서의 한 절에만 걸리는 키워드를 심는다 — 예산이 작아도 그 절은 살아야 한다.
        docs[-1][0].tree["structure"][7]["title"] = "롤백 절차"
        catalog, _, _ = _render_catalog(docs, ["롤백"], budget=2_000)
        assert "롤백 절차" in catalog, catalog[:500]

    def test_no_keyword_match_falls_back_to_shallow_sweep(self) -> None:
        # 키워드가 하나도 안 걸려도 빈 트리를 주면 안 된다 — 얕게라도 넓게 훑는다.
        catalog, examined, _ = _render_catalog(self._docs(30), ["없는키워드"], budget=5_000)
        assert catalog.strip(), "폴백이 빈 카탈로그를 만들었다"
        assert examined >= 1

    def test_empty_corpus_is_empty_catalog(self) -> None:
        assert _render_catalog([], ["배포"], budget=60_000) == ("", 0, 0)
