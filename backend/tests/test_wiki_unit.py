"""위키 컴파일/검색 순수 로직 단위 테스트 (Phase 7-3).

DB 없이 검증 가능한 부분만 다룬다: 컴파일 규칙 프롬프트 조립, 분석 JSON 파싱 폴백, frontmatter
파싱/직렬화, locked 페이지 cascade 제외, index.md/log.md 부기, 페이지 정규화/스텁, 위키 우선
검색 배분. 그룹 read 판정 헬퍼·스페이스 접근 목록 등 DB 의존 로직은 integration_wiki 에서 검증한다.
"""

from __future__ import annotations

from app.services import wiki_compile as wc
from app.services.retrieval import RetrievedChunk, prioritize_wiki

# --- 컴파일 규칙 프롬프트 조립 -----------------------------------------------


class TestPromptAssembly:
    def test_generation_prompt_injects_all_six_rules(self) -> None:
        plan = wc.AnalysisPlan(primary_title="정책", summary="요약")
        prompt = wc.build_generation_prompt(
            plan, existing_page_md=None, source_name="doc.txt", source_text="본문 내용"
        )
        # 컴파일 규칙 6항이 모두 시스템 프롬프트에 주입돼야 한다(PRD 3.7.1).
        for n in range(1, 7):
            assert f"{n}." in prompt
        assert "locked: true" in prompt  # 규칙 5 — 잠금 페이지 보호 명시
        assert "See Also" in prompt  # 규칙 3
        # 소스는 데이터 구분자로 감싸 프롬프트 인젝션을 방어한다.
        assert '<소스 파일명="doc.txt">' in prompt
        assert "본문 내용" in prompt

    def test_generation_prompt_includes_existing_page(self) -> None:
        plan = wc.AnalysisPlan(primary_title="정책", summary="요약")
        prompt = wc.build_generation_prompt(
            plan, existing_page_md="# 기존\n오래된 내용", source_name="d.txt", source_text="x"
        )
        assert "기존" in prompt and "오래된 내용" in prompt

    def test_analysis_prompt_has_catalog_and_source(self) -> None:
        prompt = wc.build_analysis_prompt(
            "# 카탈로그\n- [A](A.md)", "src.txt", "소스 텍스트"
        )
        assert "카탈로그" in prompt and "[A](A.md)" in prompt
        assert '<소스 파일명="src.txt">' in prompt
        assert "JSON" in prompt


# --- 분석 JSON 파싱 폴백 -----------------------------------------------------


class TestAnalysisParse:
    def test_parses_plain_json(self) -> None:
        raw = (
            '{"primary_title": "매출 보고", "summary": "3분기 요약",'
            ' "concepts": ["매출", "성장"], "related_titles": ["재무.md"]}'
        )
        plan = wc.parse_analysis_plan(raw, fallback_title="T", fallback_summary="S")
        assert plan.primary_title == "매출 보고"
        assert plan.summary == "3분기 요약"
        assert plan.concepts == ["매출", "성장"]
        assert plan.related_titles == ["재무.md"]

    def test_parses_fenced_json(self) -> None:
        raw = '설명\n```json\n{"primary_title": "제목"}\n```\n끝'
        plan = wc.parse_analysis_plan(raw, fallback_title="T", fallback_summary="S")
        assert plan.primary_title == "제목"

    def test_falls_back_on_garbage(self) -> None:
        plan = wc.parse_analysis_plan(
            "완전히 JSON 아님", fallback_title="폴백제목", fallback_summary="폴백요약"
        )
        assert plan.primary_title == "폴백제목"
        assert plan.summary == "폴백요약"

    def test_falls_back_on_empty(self) -> None:
        plan = wc.parse_analysis_plan("", fallback_title="T", fallback_summary="S")
        assert plan.primary_title == "T" and plan.summary == "S"


# --- frontmatter / locked --------------------------------------------------


class TestFrontmatter:
    def test_roundtrip(self) -> None:
        meta = {"title": "제목", "locked": True, "sources": ["a.txt", "b.txt"]}
        md = wc.render_page(meta, "본문입니다.")
        parsed, body = wc.parse_frontmatter(md)
        assert parsed["title"] == "제목"
        assert parsed["locked"] is True
        assert parsed["sources"] == ["a.txt", "b.txt"]
        assert body.strip() == "본문입니다."

    def test_no_frontmatter(self) -> None:
        meta, body = wc.parse_frontmatter("# 그냥 본문\n내용")
        assert meta == {}
        assert body == "# 그냥 본문\n내용"

    def test_is_locked(self) -> None:
        assert wc.is_locked("---\nlocked: true\n---\n본문")
        assert not wc.is_locked("---\nlocked: false\n---\n본문")
        assert not wc.is_locked("# frontmatter 없음")


# --- cascade 후보 (locked 제외) ---------------------------------------------


class TestCascade:
    def test_excludes_locked_index_log_and_primary(self) -> None:
        pages = [
            wc.PageRef("index.md", "# 카탈로그"),
            wc.PageRef("log.md", "# 로그"),
            wc.PageRef("primary.md", "---\nlocked: false\n---\n1차"),
            wc.PageRef("locked.md", "---\nlocked: true\n---\n사람 확정"),
            wc.PageRef("free.md", "---\nlocked: false\n---\n갱신 가능"),
        ]
        got = {p.name for p in wc.cascade_candidates(pages, "primary.md")}
        assert got == {"free.md"}, "index/log·1차·locked 제외, 자유 페이지만 후보"


# --- 페이지 정규화 / 스텁 ----------------------------------------------------


class TestPageNormalize:
    def test_wraps_missing_frontmatter(self) -> None:
        md = wc.normalize_page("# 제목\n본문만 있음", title="제목", source_name="s.txt")
        meta, body = wc.parse_frontmatter(md)
        assert meta["locked"] is False
        assert meta["title"] == "제목"
        assert "s.txt" in [str(x) for x in meta["sources"]]
        assert "본문만 있음" in body

    def test_forces_locked_false_and_adds_source(self) -> None:
        raw = "---\ntitle: T\nsources: [other.txt]\n---\n본문"
        md = wc.normalize_page(raw, title="T", source_name="new.txt")
        meta, _ = wc.parse_frontmatter(md)
        assert meta["locked"] is False
        sources = [str(x) for x in meta["sources"]]
        assert "other.txt" in sources and "new.txt" in sources

    def test_strips_code_fence(self) -> None:
        raw = "```markdown\n# 제목\n본문\n```"
        md = wc.normalize_page(raw, title="제목", source_name="s.txt")
        assert "```" not in md
        assert "본문" in md

    def test_stub_page_is_valid(self) -> None:
        plan = wc.AnalysisPlan(primary_title="스텁", summary="요약", concepts=["개념1"])
        md = wc.stub_page_markdown(plan, "s.txt", "소스 텍스트 내용")
        meta, body = wc.parse_frontmatter(md)
        assert meta["title"] == "스텁"
        assert meta["locked"] is False
        assert "s.txt" in [str(x) for x in meta["sources"]]
        assert "요약" in body


class TestFilename:
    def test_sanitizes(self) -> None:
        assert wc.page_filename("보고서/2026") == "보고서_2026.md"
        assert wc.page_filename("  제목  ") == "제목.md"
        assert wc.page_filename("") == "page.md"

    def test_derive_title(self) -> None:
        assert wc.derive_title("report.txt") == "report"
        assert wc.derive_title("noext") == "noext"


# --- index.md / log.md 부기 --------------------------------------------------


class TestBookkeeping:
    def test_upsert_index_add_then_update(self) -> None:
        idx = wc.upsert_index_entry("", "정책", "정책.md", "첫 요약")
        assert "- [정책](정책.md) — 첫 요약" in idx
        entries = wc.index_entries(idx)
        assert any("정책.md" in e for e in entries)
        # 같은 파일명 재-upsert → 교체(중복 없음).
        idx2 = wc.upsert_index_entry(idx, "정책", "정책.md", "새 요약")
        assert idx2.count("정책.md") == 1
        assert "새 요약" in idx2

    def test_log_append_and_recent(self) -> None:
        log = wc.append_log("# 로그", wc.log_entry("ingest", "A.md", ["B.md", "C.md"]))
        log = wc.append_log(log, wc.log_entry("lint", "요약", []))
        assert "primary=[A.md]" in log
        assert "cascade=[B.md,C.md]" in log
        recent = wc.recent_log_entries(log, 10)
        # 최신순 — lint 가 먼저.
        assert "lint" in recent[0]
        assert len(recent) == 2

    def test_ensure_see_also_idempotent_and_locked(self) -> None:
        page = "---\nlocked: false\n---\n# P\n내용"
        once = wc.ensure_see_also(page, "대상", "대상.md")
        assert "[대상](대상.md)" in once
        # 이미 있으면 중복 추가하지 않는다.
        twice = wc.ensure_see_also(once, "대상", "대상.md")
        assert twice.count("[대상](대상.md)") == 1
        # locked 페이지는 건드리지 않는다(규칙 5).
        locked = "---\nlocked: true\n---\n# L"
        assert wc.ensure_see_also(locked, "대상", "대상.md") == locked


# --- 위키 우선 검색 배분 (PRD 3.7.5) ----------------------------------------


def _chunk(file_id: int, distance: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=file_id * 10,
        file_id=file_id,
        file_name=f"f{file_id}",
        version=1,
        content="c",
        distance=distance,
    )


class TestWikiPriority:
    def test_allocates_half_to_wiki(self) -> None:
        # 원문 1,2,3(가까움) + 위키 10,11(멀어도 우선). k=4 → 절반(2)은 위키 우선 할당.
        chunks = [
            _chunk(1, 0.1),
            _chunk(2, 0.2),
            _chunk(3, 0.3),
            _chunk(10, 0.8),
            _chunk(11, 0.9),
        ]
        result = prioritize_wiki(chunks, wiki_page_ids={10, 11}, k=4)
        ids = {c.file_id for c in result}
        assert 10 in ids and 11 in ids, "위키 청크가 절반 우선 할당돼야 한다"
        assert len(result) == 4
        # 최종은 거리 오름차순 정렬.
        assert [c.distance for c in result] == sorted(c.distance for c in result)

    def test_no_wiki_ids_returns_top_k(self) -> None:
        chunks = [_chunk(i, i * 0.1) for i in range(1, 6)]
        result = prioritize_wiki(chunks, wiki_page_ids=set(), k=2)
        assert [c.file_id for c in result] == [1, 2]

    def test_fills_from_source_when_few_wiki(self) -> None:
        chunks = [_chunk(1, 0.1), _chunk(2, 0.2), _chunk(10, 0.5)]
        result = prioritize_wiki(chunks, wiki_page_ids={10}, k=3)
        assert {c.file_id for c in result} == {1, 2, 10}
