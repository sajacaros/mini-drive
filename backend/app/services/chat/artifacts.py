"""답변의 **형태** — 렌더 툴의 인자 스키마가 곧 프론트의 렌더링 계약이다.

**왜 앞단에서 인텐트를 분류하지 않는가.** "비교/분석/트렌드"를 먼저 분류해 그래프를 가르면
몸통이 90% 겹치는 그래프가 형태 수만큼 생기고, 분류기가 틀리는 순간 전체가 틀린다. 분류는
생성이 아니라 판정이라 Solar 에서 `reasoning_effort` 를 올려야 하므로 호출도 느려진다.

대신 **형태 자체를 툴로 만든다.** 모델이 형태가 필요하다고 판단하면 `answer_*` 중 하나를
호출하고, 그 인자가 곧 렌더링할 데이터다. 형태가 필요 없으면 그냥 평문으로 답하고 그것이
텍스트 아티팩트가 된다(실측상 이쪽이 더 흔하다 — `agent.py` 모듈 주석). 얻는 것 셋:

- 검색 툴과 같은 기계를 쓴다 — 구조화 출력(guided decoding)이라는 두 번째 경로가 없다.
- 형태를 늘리는 일이 **이 파일에 클래스 하나 + 프론트에 렌더러 하나**로 끝난다.
- 모델이 **검색 결과를 본 뒤에** 형태를 정한다. 사용자가 "비교해줘"라고 말하지 않아도
  비교하기 좋은 자료가 나오면 표가 된다.

`kind` 는 클래스가 아니라 데이터에 박아 저장한다(`chat_messages.artifact`). 저장된 대화를
다시 그릴 때 파이썬 클래스를 되살릴 필요가 없어야 하기 때문이다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# 표가 감당할 수 있는 크기. 넘으면 자른다 — 모델이 문서 전체를 표로 펴려 들 때 화면과
# 저장 양쪽이 무너지는 것을 막는다. 자른 사실은 note 로 사용자에게 보인다.
MAX_ROWS = 50
MAX_COLUMNS = 8


class AnswerText(BaseModel):
    """일반 답변. 근거를 문장 안에 자연스럽게 밝힌 마크다운 한 덩어리."""

    markdown: str = Field(
        description="한국어 마크다운 답변. 표로 견주는 편이 나으면 answer_comparison 을 쓰시오."
    )


class AnswerComparison(BaseModel):
    """항목을 나란히 놓고 견주는 답변. 표 하나와 짧은 해설."""

    columns: list[str] = Field(
        description="표의 열 이름. 첫 열은 비교 대상의 이름이어야 한다. 예: 항목, 2025, 2026"
    )
    rows: list[list[str]] = Field(
        description="표의 행. 각 행의 길이는 columns 와 같아야 한다. 값이 없으면 빈 문자열."
    )
    title: str = Field(default="", description="표 제목. 없어도 된다.")
    note: str = Field(
        default="",
        description="표만으로 드러나지 않는 해설이나 단서. 없어도 된다.",
    )


# 렌더 툴 이름 → 인자 스키마. 에이전트가 모델에 노출할 목록이자, 결과를 아티팩트로 바꾸는 표다.
# **여기에 한 줄 추가하는 것이 형태를 하나 늘리는 일의 전부다**(프론트 렌더러는 별도).
RENDERERS: dict[str, type[BaseModel]] = {
    "answer_text": AnswerText,
    "answer_comparison": AnswerComparison,
}


def _clip_comparison(data: dict[str, Any]) -> dict[str, Any]:
    """표를 화면과 저장이 감당할 크기로 줄이고, **줄였다는 사실을 표에 남긴다.**

    조용히 자르면 사용자는 그것을 완전한 표로 읽는다. 그건 위키 질의가 "찾아본 자료 중에는
    없습니다"라고 답하는 이유와 같은 원칙이다 — 좁혀 본 것을 전부로 말하지 않는다.
    """
    columns = [str(c) for c in data.get("columns") or []][:MAX_COLUMNS]
    width = len(columns)
    rows: list[list[str]] = []
    for raw in (data.get("rows") or [])[:MAX_ROWS]:
        cells = [str(c) for c in (raw if isinstance(raw, list) else [raw])][:width]
        # 짧은 행을 버리지 않고 채운다 — 모델이 빈 칸을 생략하는 일이 흔하고, 그 행에도
        # 정보가 있다.
        cells += [""] * (width - len(cells))
        rows.append(cells)

    dropped = max(len(data.get("rows") or []) - MAX_ROWS, 0)
    note = str(data.get("note") or "")
    if dropped:
        suffix = f"표가 길어 {dropped}행을 줄였습니다."
        note = f"{note} {suffix}".strip()

    return {
        "kind": "comparison",
        "columns": columns,
        "rows": rows,
        "title": str(data.get("title") or ""),
        "note": note,
    }


def build(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """렌더 툴 호출을 저장·전송할 아티팩트로 바꾼다.

    모델이 보낸 인자를 그대로 믿지 않는다 — 스키마를 줘도 열 수와 행 길이가 어긋나는 일이
    있고, 그건 프론트에서 표가 깨지는 것으로 나타난다. 여기서 한 번 다듬으면 화면은 항상
    정합한 데이터만 본다.
    """
    schema = RENDERERS.get(tool_name)
    if schema is None:
        raise ValueError(f"알 수 없는 렌더 툴: {tool_name}")
    data = schema.model_validate(args).model_dump()
    if tool_name == "answer_comparison":
        return _clip_comparison(data)
    return {"kind": "text", "markdown": str(data.get("markdown") or "")}


def text(markdown: str) -> dict[str, Any]:
    """폴백 아티팩트 — 툴 루프가 형태를 못 정하고 끝났을 때 쓴다."""
    return {"kind": "text", "markdown": markdown}


def summarize(artifact: dict[str, Any]) -> str:
    """아티팩트에서 목록 미리보기·전문 검색용 평문을 뽑는다.

    `chat_messages.content` 를 항상 채우기 위한 것이다 — 목록 화면이 형태별 분기 없이
    한 컬럼만 읽으면 되게 한다.
    """
    kind = artifact.get("kind")
    if kind == "comparison":
        head = str(artifact.get("title") or "")
        columns = " · ".join(str(c) for c in artifact.get("columns") or [])
        rows = len(artifact.get("rows") or [])
        body = f"[비교표] {columns} ({rows}행)"
        note = str(artifact.get("note") or "")
        return " ".join(p for p in (head, body, note) if p).strip()
    return str(artifact.get("markdown") or "")


__all__ = [
    "MAX_COLUMNS",
    "MAX_ROWS",
    "AnswerComparison",
    "AnswerText",
    "RENDERERS",
    "build",
    "summarize",
    "text",
]
