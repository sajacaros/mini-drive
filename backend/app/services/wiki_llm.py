"""위키 인덱싱·질의의 LLM 호출 (spec/wiki-index.md).

접속과 재시도 규율은 `services/llm.py` 가 쥐고, 이 모듈은 **인덱싱 계열의 호출 관례**만
남긴다 — 단발 프롬프트를 문자열로 받고, 기본 `reasoning_effort` 가 인덱싱 쪽 값이다.

**`reasoning_effort` 를 호출 지점별로 나눈다.** Solar-Open2 는 reasoning 모델이라 기본값에서
호출당 수백~수천 추론 토큰을 태운다(실측: 요약 1건 11.4초/911토큰 → low 는 0.7초/44토큰).
생성 계열은 low 로 내려도 품질이 유지되지만, 판정 계열은 low 에서 오판한다 — 목차가
`3-1 → 3-3` 으로 건너뛴 문서에서 low 는 "3-2 누락 = 불완전"으로 5/5 잘못 판단했고 medium
이상이 정확했다. 인덱싱은 생성만 하므로 low 를 쓰고, 판정이 필요한 곳(대화형 질의의 툴 선택
등)은 호출자가 effort 를 올린다 — 채팅 기본값이 medium 인 이유다(`chat_llm_reasoning_effort`).

`WikiLLMError` 를 `services/llm.py` 의 예외에 붙여 둔 것은 호출부(`api/routes/wiki.py`)의
except 절을 그대로 두기 위해서다.
"""

from __future__ import annotations

from app.services.llm import LLMError, LLMRequestError, health
from app.services.llm import complete as _complete

# 인덱싱 경로가 쓰던 이름을 유지한다. 상속이라 기존 `except WikiLLMError` 가 그대로 잡는다.
WikiLLMError = LLMError
WikiLLMRequestError = LLMRequestError


async def complete(
    prompt: str,
    *,
    reasoning_effort: str | None = None,
    timeout: float = 120.0,
    max_retries: int = 3,
) -> str:
    """단발 프롬프트를 보내고 최종 응답 텍스트를 돌려준다."""
    return await _complete(
        [{"role": "user", "content": prompt}],
        reasoning_effort=reasoning_effort,
        timeout=timeout,
        max_retries=max_retries,
    )


__all__ = ["WikiLLMError", "WikiLLMRequestError", "complete", "health"]
