"""재색인 요약 캐시 단위 테스트 (DB 불필요, LLM 은 대역) — spec/wiki-index.md 「GPU 경합」.

재색인 비용의 거의 전부가 노드별 요약 LLM 호출이다. 캐시가 실제로 **호출을 줄이는지**,
그리고 줄이지 말아야 할 때 줄이지 않는지를 고정한다. 해시 자체의 성질은
`test_wiki_tree_unit.py::TestSummaryCache` 가 본다.

가장 중요한 축은 마지막 것이다 — LLM 이 잠깐 죽어 본문 앞부분으로 대체한 요약이 캐시에
들어가면, 그 400자가 그 절의 요약으로 영구히 굳는다.
"""

from __future__ import annotations

import pytest

from app.services import wiki_indexer, wiki_llm, wiki_tree

# 요약 임계값을 넘겨야 LLM 경로를 탄다. 짧은 절은 본문을 그대로 요약으로 쓴다.
LONG = "가" * (wiki_tree.SHORT_NODE_CHARS + 50)
DOC = f"# 문서\n\n머리말.\n\n## 절 하나\n\n{LONG}\n\n## 절 둘\n\n{LONG}\n"


class _Recorder:
    """`wiki_llm.complete` 대역 — 호출 횟수를 센다."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def __call__(self, prompt: str, **kwargs: object) -> str:
        self.calls += 1
        if self.fail:
            raise wiki_llm.WikiLLMError("모델 서버에 연결할 수 없습니다")
        return f"요약 {self.calls}"


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(wiki_indexer.wiki_llm, "complete", rec)
    return rec


async def _summarize(cache: dict[str, str] | None) -> tuple[list[wiki_tree.TreeNode], int, int]:
    roots, lines = wiki_tree.parse(DOC, doc_name="doc")
    nodes = wiki_tree.flatten(roots)
    calls, hits = await wiki_indexer._summarize(nodes, lines, cache=cache)
    return nodes, calls, hits


class TestCacheReuse:
    async def test_cold_cache_calls_llm_for_every_long_node(self, llm: _Recorder) -> None:
        _, calls, hits = await _summarize(None)
        assert calls == 2 and hits == 0
        assert llm.calls == 2

    async def test_warm_cache_skips_the_llm_entirely(self, llm: _Recorder) -> None:
        nodes, _, _ = await _summarize(None)
        warm = {n.text_hash: n.summary for n in nodes if n.summary and n.text_hash}

        llm.calls = 0
        _, calls, hits = await _summarize(warm)
        assert calls == 0 and hits == 2
        assert llm.calls == 0, "안 바뀐 절을 다시 요약했다"

    async def test_only_the_changed_section_is_resummarized(
        self, llm: _Recorder
    ) -> None:
        # 재색인이 노리는 상황 — 절 하나만 고쳐 다시 올린다.
        nodes, _, _ = await _summarize(None)
        warm = {n.text_hash: n.summary for n in nodes if n.summary and n.text_hash}

        edited = DOC.replace("## 절 둘\n\n" + LONG, "## 절 둘\n\n" + LONG + "추가된 문장.")
        roots, lines = wiki_tree.parse(edited, doc_name="doc")
        llm.calls = 0
        calls, hits = await wiki_indexer._summarize(
            wiki_tree.flatten(roots), lines, cache=warm
        )
        assert calls == 1 and hits == 1, "고친 절 하나만 다시 요약해야 한다"

    async def test_short_nodes_never_touch_the_llm(self, llm: _Recorder) -> None:
        roots, lines = wiki_tree.parse("# 짧다\n\n한 줄.\n", doc_name="doc")
        calls, _ = await wiki_indexer._summarize(wiki_tree.flatten(roots), lines)
        assert calls == 0 and llm.calls == 0


class TestFallbackIsNotCached:
    async def test_failed_summary_clears_the_hash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # LLM 이 죽은 회차의 본문 앞부분 400자가 그 절의 요약으로 굳으면 안 된다.
        monkeypatch.setattr(wiki_indexer.wiki_llm, "complete", _Recorder(fail=True))
        nodes, _, _ = await _summarize(None)
        long_nodes = [n for n in nodes if n.summary and len(n.summary) >= 100]
        assert long_nodes, "요약 대상 절이 없다 — 픽스처 문제"
        assert all(not n.text_hash for n in long_nodes), "대체물이 캐시 열쇠를 갖고 있다"

    async def test_next_run_resummarizes_after_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing = _Recorder(fail=True)
        monkeypatch.setattr(wiki_indexer.wiki_llm, "complete", failing)
        nodes, _, _ = await _summarize(None)
        lines = DOC.split("\n")
        stale_cache = wiki_tree.summary_cache(
            wiki_tree.to_dict(
                [n for n in nodes if n.level == 1], doc_name="doc", line_count=len(lines)
            )
        )

        recovered = _Recorder()
        monkeypatch.setattr(wiki_indexer.wiki_llm, "complete", recovered)
        _, calls, hits = await _summarize(stale_cache)
        assert hits == 0 and calls == 2, "실패한 회차의 대체물을 물려받았다"
