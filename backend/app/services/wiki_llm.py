"""사내 vLLM 호출 (spec/wiki-index.md).

vLLM 이 OpenAI 호환 서버라 `httpx` 로 직접 부른다 — httpx 는 이미 런타임 의존성이라 새로
추가할 것이 없다(LiteLLM 불필요).

**`reasoning_effort` 를 호출 지점별로 나눈다.** Solar-Open2 는 reasoning 모델이라 기본값에서
호출당 수백~수천 추론 토큰을 태운다(실측: 요약 1건 11.4초/911토큰 → low 는 0.7초/44토큰).
생성 계열은 low 로 내려도 품질이 유지되지만, 판정 계열은 low 에서 오판한다 — 목차가
`3-1 → 3-3` 으로 건너뛴 문서에서 low 는 "3-2 누락 = 불완전"으로 5/5 잘못 판단했고 medium
이상이 정확했다. v1 의 인덱싱은 생성만 하므로 low 를 쓰고, 판정이 필요한 곳(질의 등)은
호출자가 effort 를 올린다.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class WikiLLMError(Exception):
    """LLM 호출 실패 — 재시도를 소진했거나 응답이 비어 있다."""


class WikiLLMRequestError(WikiLLMError):
    """LLM 이 요청 자체를 거부했다 (4xx). **재시도해도 같은 결과다.**

    호출자가 "서버에 연결할 수 없다"와 구분해야 하는 실패다 — 서버는 정상이고 우리가 보낸
    것이 잘못됐다. 프롬프트가 컨텍스트를 넘긴 경우(400)가 여기로 온다.
    """


def _endpoint() -> str:
    return settings.wiki_llm_base_url.rstrip("/") + "/chat/completions"


def _model_name() -> str:
    """LiteLLM 프리픽스(`hosted_vllm/`)가 붙어 있으면 떼고 서버에 보낸다."""
    model = settings.wiki_llm_model
    return model.split("/", 1)[1] if "/" in model else model


async def complete(
    prompt: str,
    *,
    reasoning_effort: str | None = None,
    timeout: float = 120.0,
    max_retries: int = 3,
) -> str:
    """단발 프롬프트를 보내고 최종 응답 텍스트를 돌려준다.

    reasoning 모델은 추론 트레이스를 `reasoning_content` 로 분리해 보내고 `content` 에는 최종
    답만 담는다 — 그래서 `content` 만 읽으면 된다(실측 확인).
    """
    payload = {
        "model": _model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "reasoning_effort": reasoning_effort or settings.wiki_llm_reasoning_effort,
    }
    headers = {"Content-Type": "application/json"}
    if settings.wiki_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.wiki_llm_api_key}"

    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(_endpoint(), json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if content:
                return content
            last = WikiLLMError("빈 응답")
        except httpx.HTTPStatusError as exc:
            # **4xx 는 재시도하지 않는다.** 우리 요청이 잘못된 것이라 같은 요청을 다시 보내면
            # 같은 응답이 온다. 프롬프트가 컨텍스트를 넘었을 때(400) 900KB 짜리 본문을 세 번 더
            # 올리는 것이 유일한 효과였다(spec/wiki-index.md 「후보 선별」). 429 만 예외다 —
            # 그건 우리 요청이 아니라 서버 혼잡이라 기다리면 통한다.
            status = exc.response.status_code
            if 400 <= status < 500 and status != 429:
                body = exc.response.text[:500]
                log.warning("wiki_llm_bad_request", status=status, body=body)
                raise WikiLLMRequestError(f"LLM 이 요청을 거부했다 ({status}): {body}") from exc
            last = exc
        except Exception as exc:  # noqa: BLE001 - 네트워크/파싱 실패를 모두 재시도로 흡수
            last = exc
        if attempt < max_retries - 1:
            # 인덱싱은 배치라 급하지 않다. 서버가 붐빌 때 몰아치지 않도록 뒤로 미룬다.
            await asyncio.sleep(2**attempt)

    raise WikiLLMError(f"LLM 호출 실패 ({max_retries}회 시도): {last}") from last


async def health() -> bool:
    """서버가 응답하는지 (사이드카 기동 로그용). 실패해도 예외를 내지 않는다."""
    try:
        headers = {}
        if settings.wiki_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.wiki_llm_api_key}"
        base = settings.wiki_llm_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/models", headers=headers)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 - 관측용이라 실패가 기능을 막지 않는다
        return False


__all__ = ["WikiLLMError", "complete", "health"]
