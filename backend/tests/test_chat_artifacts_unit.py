"""아티팩트(답변 형태) 단위 테스트 — 렌더 툴 인자가 렌더링 계약이 되는 지점.

검증 축: 툴 이름→스키마 표(RENDERERS)와 실제 툴 스펙이 어긋나지 않는가 / 표 정규화(열 수에
맞춘 패딩·자르기)와 **자른 사실의 고지** / 목록 미리보기용 평문 추출.

여기서 스펙 생성을 함께 보는 이유는, 형태를 늘릴 때 `RENDERERS` 에 한 줄 넣는 것만으로
모델에 노출되는 툴까지 따라오는지가 이 설계의 핵심 약속이기 때문이다.
"""

from __future__ import annotations

import pytest

from app.services.chat import artifacts, tools


def test_every_renderer_becomes_a_tool_spec():
    """RENDERERS 에 넣으면 모델에 노출되는 툴이 자동으로 생긴다 — 형태 추가 비용의 근거."""
    names = {s["function"]["name"] for s in tools.specs()}
    assert set(artifacts.RENDERERS) <= names
    assert tools.SEARCH_WIKI in names


def test_render_only_specs_drop_the_search_tool():
    """왕복 상한에서 쓰는 목록에는 검색이 없다 — 더 찾으러 갈 길을 막는 것이 목적이다."""
    names = {s["function"]["name"] for s in tools.specs(render_only=True)}
    assert names == set(artifacts.RENDERERS)


def test_tool_specs_carry_parameter_schemas():
    """모델이 인자를 만들려면 스키마가 실려 있어야 한다."""
    by_name = {s["function"]["name"]: s for s in tools.specs()}
    params = by_name["answer_comparison"]["function"]["parameters"]
    assert params["type"] == "object"
    assert {"columns", "rows"} <= set(params["properties"])
    assert by_name["answer_comparison"]["function"]["description"]


def test_text_artifact_roundtrip():
    assert artifacts.build("answer_text", {"markdown": "안녕"}) == {
        "kind": "text",
        "markdown": "안녕",
    }


def test_short_rows_are_padded_not_dropped():
    """모델이 빈 칸을 생략하는 일이 흔하다 — 그 행에도 정보가 있으므로 버리지 않는다."""
    art = artifacts.build(
        "answer_comparison",
        {"columns": ["항목", "2025", "2026"], "rows": [["예산", "10억"]]},
    )
    assert art["rows"] == [["예산", "10억", ""]]


def test_long_rows_are_trimmed_to_column_count():
    art = artifacts.build(
        "answer_comparison",
        {"columns": ["a", "b"], "rows": [["1", "2", "3", "4"]]},
    )
    assert art["rows"] == [["1", "2"]]


def test_row_cap_is_disclosed_in_note():
    """조용히 자르면 완전한 표로 읽힌다 — 자른 사실이 화면에 남아야 한다."""
    rows = [["x", "y"]] * (artifacts.MAX_ROWS + 3)
    art = artifacts.build(
        "answer_comparison", {"columns": ["a", "b"], "rows": rows, "note": "원래 메모"}
    )
    assert len(art["rows"]) == artifacts.MAX_ROWS
    assert "원래 메모" in art["note"] and "3행을 줄였습니다" in art["note"]


def test_column_cap_applies():
    art = artifacts.build(
        "answer_comparison",
        {"columns": [str(i) for i in range(artifacts.MAX_COLUMNS + 4)], "rows": []},
    )
    assert len(art["columns"]) == artifacts.MAX_COLUMNS


def test_unknown_tool_is_rejected():
    with pytest.raises(ValueError):
        artifacts.build("answer_chart", {})


def test_summarize_fills_content_for_every_kind():
    """`chat_messages.content` 는 항상 채워진다 — 목록이 형태별 분기 없이 한 컬럼만 읽는다."""
    assert artifacts.summarize({"kind": "text", "markdown": "본문"}) == "본문"
    summary = artifacts.summarize(
        {
            "kind": "comparison",
            "title": "연도 대조",
            "columns": ["항목", "2025"],
            "rows": [["예산", "10억"]],
            "note": "단서",
        }
    )
    assert "연도 대조" in summary and "항목 · 2025" in summary and "단서" in summary
