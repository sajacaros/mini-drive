"""LLM 접속 계층 — provider 설정과 호출 규율을 한 곳에 모은다.

**왜 LiteLLM 인가.** 이 코드는 원래 httpx 로 OpenAI 호환 서버를 직접 불렀다. 사내 vLLM 하나만
보면 그게 가장 가벼웠고, 실제로 모델 문자열은 이미 `hosted_vllm/solar-open2-250b` 라는 LiteLLM
규약을 쓰고 `.env.llm` 에 `HOSTED_VLLM_API_BASE` 가 있었다 — 규약만 빌려 쓰고 라이브러리는 안
쓰는 상태였다. 모델을 갈아탈 수 있어야 한다는 요구가 생기면서 그 절충이 깨진다. Anthropic·
OpenAI·Gemini 는 인증 헤더도 요청 형태도 제각각이라 httpx 로 붙이면 provider 마다 분기가
생기고, 그 분기가 곧 LiteLLM 이 이미 하는 일이다.

**호출 규율은 우리가 쥔다.** `num_retries=0` 으로 LiteLLM 내장 재시도를 끄고 아래 루프를 쓴다.
"4xx 는 재시도하지 않고 429 만 재시도한다"는 규칙이 실측에서 나온 것이라 라이브러리 기본값에
넘길 수 없다 — 프롬프트가 컨텍스트를 넘겨 400 이 났을 때(2026-07-30) 900KB 짜리 본문을 세 번
더 올리는 것이 유일한 효과였다.

**상태 코드로 분류하고 예외 클래스로 분류하지 않는다.** LiteLLM 은 provider 예외를 자기 계층
구조로 감싸는데, 그 계층은 버전과 provider 에 따라 움직인다. `status_code` 속성은 안 움직인다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import litellm
from langchain_litellm import ChatLiteLLM

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# LiteLLM 은 기본적으로 기동/실패 시 배너와 힌트를 stdout 으로 찍는다. 구조화 로그(structlog)를
# 쓰는 서비스라 그 출력이 로그 파이프라인을 오염시킨다.
litellm.suppress_debug_info = True
# **파라미터를 조용히 버리지 않는다.** drop_params=True 면 provider 가 모르는 인자를 말없이
# 떨어뜨리는데, 여기서 그 인자는 reasoning_effort 다 — 판정 호출이 low 로 돌아간 것과
# 구분되지 않는 채 품질만 나빠진다. 지원하지 않으면 시끄럽게 실패하는 편이 낫다.
litellm.drop_params = False


class LLMError(Exception):
    """LLM 호출 실패 — 재시도를 소진했거나 응답이 비어 있다."""


class LLMRequestError(LLMError):
    """LLM 이 요청 자체를 거부했다 (4xx). **재시도해도 같은 결과다.**

    호출자가 "서버에 연결할 수 없다"와 구분해야 하는 실패다 — 서버는 정상이고 우리가 보낸
    것이 잘못됐다. 프롬프트가 컨텍스트를 넘긴 경우(400)가 여기로 온다.
    """


def chat_model() -> str:
    """대화 경로가 쓸 모델. 비어 있으면 인덱싱과 같은 모델을 쓴다."""
    return settings.chat_llm_model or settings.wiki_llm_model


def _auth() -> dict[str, Any]:
    """provider 접속 인자. base_url 이 비어 있으면 넣지 않는다 — 사내 vLLM 이 아닌 provider
    (예: `anthropic/claude-...`)로 갈아탈 때 LiteLLM 이 자기 기본 엔드포인트를 쓰게 둔다.
    """
    out: dict[str, Any] = {}
    if settings.wiki_llm_base_url:
        out["api_base"] = settings.wiki_llm_base_url
    if settings.wiki_llm_api_key:
        out["api_key"] = settings.wiki_llm_api_key
    return out


def _provider(model: str) -> dict[str, Any]:
    """프리픽스 없는 모델명을 구제한다.

    LiteLLM 은 `hosted_vllm/solar-open2-250b` 처럼 프리픽스로 provider 를 정한다. 그런데 이
    코드가 httpx 로 직접 부르던 시절에는 **프리픽스를 떼고** 서버에 보냈기 때문에, 기존 배포의
    `WIKI_LLM_MODEL` 에는 프리픽스가 없는 값이 들어 있을 수 있다(실제로 이 리포의 `.env` 가
    그렇다). 그대로 넘기면 "LLM Provider NOT provided" 로 전부 죽는다.

    base_url 이 있다는 것은 OpenAI 호환 자체 호스팅을 가리키므로 `hosted_vllm` 으로 본다.
    base_url 이 없으면 SaaS provider 라는 뜻이고, 그때는 프리픽스가 반드시 있어야 하므로
    LiteLLM 이 내는 오류가 맞는 오류다.
    """
    if "/" in model or not settings.wiki_llm_base_url:
        return {}
    return {"custom_llm_provider": "hosted_vllm"}


def call_kwargs(
    *, model: str, reasoning_effort: str | None, temperature: float = 0
) -> dict[str, Any]:
    """LiteLLM 호출 인자를 조립한다. 채팅 경로(LangChain)와 단발 경로가 같은 것을 쓰도록 공유한다.

    `reasoning_effort` 가 빈 문자열이면 **키 자체를 넣지 않는다.** reasoning 모델이 아닌
    provider 로 갈아탈 때의 탈출구다(설정 주석 참조).
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "num_retries": 0,  # 재시도는 with_retry 가 쥔다.
        **_provider(model),
        **_auth(),
    }
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return kwargs


def classify(exc: Exception) -> LLMRequestError | None:
    """재시도가 무의미한 실패면 그에 맞는 예외를, 아니면 None 을 돌려준다.

    429 만 예외다 — 그건 우리 요청이 아니라 서버 혼잡이라 기다리면 통한다.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        body = str(exc)[:500]
        log.warning("llm_bad_request", status=status, body=body)
        return LLMRequestError(f"LLM 이 요청을 거부했다 ({status}): {body}")
    return None


async def with_retry[T](call: Callable[[], Awaitable[T]], *, max_retries: int = 3) -> T:
    """재시도 규율을 한 곳에 둔다 — 단발 호출과 에이전트의 모델 노드가 같은 것을 쓴다.

    4xx 는 즉시 `LLMRequestError` 로 올린다(재시도해도 같은 결과다). 429·연결 실패·타임아웃만
    지수 백오프로 다시 시도한다.
    """
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await call()
        except LLMRequestError:
            raise
        except Exception as exc:  # noqa: BLE001 - 상태 코드로 분류한다(모듈 주석).
            fatal = classify(exc)
            if fatal is not None:
                raise fatal from exc
            last = exc
        if attempt < max_retries - 1:
            # 서버가 붐빌 때 몰아치지 않도록 뒤로 미룬다.
            await asyncio.sleep(2**attempt)

    raise LLMError(f"LLM 호출 실패 ({max_retries}회 시도): {last}") from last


async def complete(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    timeout: float = 120.0,
    max_retries: int = 3,
) -> str:
    """메시지를 보내고 최종 응답 텍스트를 돌려준다.

    reasoning 모델은 추론 트레이스를 `reasoning_content` 로 분리해 보내고 `content` 에는 최종
    답만 담는다 — 그래서 `content` 만 읽으면 된다(실측 확인).
    """
    kwargs = call_kwargs(
        model=model or settings.wiki_llm_model,
        reasoning_effort=(
            reasoning_effort
            if reasoning_effort is not None
            else settings.wiki_llm_reasoning_effort
        ),
    )

    async def once() -> str:
        response = await litellm.acompletion(
            messages=messages, timeout=timeout, **kwargs
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            # 빈 응답은 실패로 본다 — 재시도 대상이라 LLMError 로 올린다.
            raise LLMError("빈 응답")
        return str(content)

    return await with_retry(once, max_retries=max_retries)


def chat_client(*, tools: list[Any] | None = None) -> Any:
    """대화 경로가 쓸 LangChain 채팅 모델. 툴을 주면 바인딩해서 돌려준다.

    `model_kwargs` 가 `litellm.acompletion` 까지 그대로 흘러가는 경로라 `reasoning_effort` 를
    여기 싣는다(langchain_litellm 의 `_default_params` 가 펼친다).

    `max_retries=1` 은 **재시도 안 함**이다(tenacity `stop_after_attempt`). 라이브러리 기본값도
    1 이지만 명시한다 — 여기 있는 재시도는 4xx 도 함께 되돌려서, "400 은 재시도하지 않는다"는
    규율을 조용히 깬다. 재시도는 `with_retry` 가 쥔다.
    """
    model = chat_model()
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_retries": 1,
        **_provider(model),
        **_auth(),
    }
    if settings.chat_llm_reasoning_effort:
        kwargs["model_kwargs"] = {
            "reasoning_effort": settings.chat_llm_reasoning_effort
        }
    client = ChatLiteLLM(**kwargs)
    return client.bind_tools(tools) if tools else client


async def health() -> bool:
    """서버가 응답하는지 (사이드카 기동 로그용). 실패해도 예외를 내지 않는다.

    LiteLLM 을 거치지 않고 `/models` 를 직접 두드린다 — 알고 싶은 것이 "모델이 답을 잘 하는가"가
    아니라 "엔드포인트가 살아 있는가"라서, 토큰을 태우지 않는 쪽이 맞다. base_url 이 없으면
    (사내 vLLM 이 아닌 provider) 확인할 대상이 없으므로 참으로 둔다.
    """
    if not settings.wiki_llm_base_url:
        return True
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


__all__ = [
    "LLMError",
    "LLMRequestError",
    "call_kwargs",
    "chat_client",
    "chat_model",
    "classify",
    "complete",
    "health",
    "with_retry",
]
