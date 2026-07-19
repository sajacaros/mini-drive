"""챗 생성 프로바이더 (PRD 3.7.4, Phase 7-2).

환경변수 `CHAT_PROVIDER` 로 고른다:

  - `vllm` (기본): 사내 vLLM 서버의 GLM 5.2(OpenAI 호환 엔드포인트)를 `langchain-openai`
    `ChatOpenAI` 에 `CHAT_BASE_URL`/`CHAT_API_KEY`/`CHAT_MODEL` 로 연결한다. 문서 컨텍스트의
    외부 전송을 최소화하려 기본을 사내 vLLM 으로 둔다.
  - `openai`: OpenAI(또는 호환) API. `CHAT_API_KEY` 필요.
  - `upstage`: Upstage solar-open2 (OpenAI 호환 엔드포인트 https://api.upstage.ai/v1). api_key 는
    `CHAT_API_KEY` 가 없으면 인덱싱용 `UPSTAGE_API_KEY` 를 폴백으로 쓰고, `CHAT_MODEL` 미지정 시
    모델은 `solar-open2` 다. egress 주의: 검색 컨텍스트가 Upstage 로 전송된다.
  - `fake`: 외부 호출이 전혀 없는 **결정적** 답변을 토큰 단위로 스트리밍한다. 검색 통과 컨텍스트
    기반으로 "다음 문서에서 찾은 내용입니다" + 상위 청크 인용을 만든다. 테스트·키 없는 개발용.

키/베이스URL(또는 upstage 는 api_key)이 없는데 vllm/openai/upstage 면 프로바이더가 None 이 되고,
챗 요청은 503("LLM 미구성")으로 **명시적으로** 실패한다(인덱싱과 달리 사용자 대면 기능이라
fail-open 이 아니다, PRD 3.7.3).

모든 프로바이더는 `astream(...)` 으로 답변 토큰(str 델타)을 비동기 스트리밍하도록 인터페이스를
통일한다 — 답변 체인에 **도구 호출을 부여하지 않는다**(프롬프트 인젝션 방어, PRD 3.7.5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import Settings
from app.core.logging import get_logger

_log = get_logger("app.chat_llm")

# 컨텍스트 블록 — (파일명, 내용). fake 프로바이더가 결정적 인용 답변을 만들 때 사용한다.
ContextBlock = tuple[str, str]

# Upstage 챗(solar) OpenAI 호환 엔드포인트. CHAT_BASE_URL 이 없으면 이 기본값을 쓴다.
_UPSTAGE_CHAT_BASE_URL = "https://api.upstage.ai/v1"

# CHAT_MODEL 미지정 시 프로바이더별 기본 모델.
_DEFAULT_CHAT_MODELS = {
    "vllm": "glm-5.2",
    "openai": "gpt-4o-mini",
    "upstage": "solar-open2",
}


class ChatProvider(Protocol):
    """챗 생성 프로바이더 인터페이스. 답변을 토큰(str 델타) 단위로 비동기 스트리밍한다."""

    # async generator 구현과 구조적으로 맞도록 일반 def 로 선언한다(반환은 AsyncIterator[str]).
    def astream(
        self,
        *,
        system_prompt: str,
        history: list[tuple[str, str]],
        question: str,
        context_blocks: list[ContextBlock],
    ) -> AsyncIterator[str]:
        """답변 토큰을 순서대로 yield 한다.

        - system_prompt: 지시문 + 컨텍스트(데이터)를 합친 시스템 프롬프트(실 LLM 용).
        - history: (role, content) 최근 대화(role 은 user/assistant).
        - question: 이번 질문.
        - context_blocks: 검색 통과 청크 (파일명, 내용) 목록(fake 결정적 답변용).
        """
        ...


def _to_langchain_messages(
    system_prompt: str, history: list[tuple[str, str]], question: str
) -> list[Any]:
    """(role, content) 히스토리 + 질문을 langchain 메시지 목록으로 변환한다."""
    from langchain_core.messages import (  # 지연 임포트(fake/유닛 경로 불요)
        AIMessage,
        HumanMessage,
        SystemMessage,
    )

    messages: list[Any] = [SystemMessage(content=system_prompt)]
    for role, content in history:
        if role == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    messages.append(HumanMessage(content=question))
    return messages


class OpenAICompatChatProvider:
    """vLLM(GLM 5.2)·OpenAI 공용 래퍼. `ChatOpenAI` 에 base_url/api_key/model 을 연결한다.

    도구 바인딩 없이 순수 생성만 스트리밍한다(프롬프트 인젝션 방어, PRD 3.7.5).
    """

    def __init__(
        self, *, model: str, api_key: str, base_url: str | None, temperature: float = 0.2
    ) -> None:
        # 지연 임포트: fake 경로/유닛 테스트가 langchain-openai 를 요구하지 않게 한다.
        from langchain_openai import ChatOpenAI

        self._client = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            streaming=True,
        )

    async def astream(
        self,
        *,
        system_prompt: str,
        history: list[tuple[str, str]],
        question: str,
        context_blocks: list[ContextBlock],
    ) -> AsyncIterator[str]:
        messages = _to_langchain_messages(system_prompt, history, question)
        async for chunk in self._client.astream(messages):
            text = chunk.content
            if isinstance(text, str) and text:
                yield text


class FakeChatProvider:
    """외부 호출 없는 결정적 챗 프로바이더(테스트·키 없는 개발용).

    검색 통과 컨텍스트에서 상위 청크를 인용해 결정적 답변을 만든다. 같은 입력은 항상 같은
    답변을 낸다. 답변을 토큰(공백 유지) 단위로 쪼개 astream 인터페이스로 스트리밍한다.
    """

    #: 인용에 포함할 상위 청크 수, 청크당 발췌 길이.
    _MAX_CITED = 3
    _SNIPPET_LEN = 80

    def _build_answer(
        self, question: str, context_blocks: list[ContextBlock]
    ) -> str:
        if not context_blocks:
            return "관련 문서를 찾지 못했습니다. 접근 가능한 문서 중에는 답변 근거가 없습니다."
        lines = ["다음 문서에서 찾은 내용입니다."]
        for file_name, content in context_blocks[: self._MAX_CITED]:
            snippet = " ".join(content.split())[: self._SNIPPET_LEN]
            lines.append(f"- [{file_name}] {snippet}")
        return "\n".join(lines)

    async def astream(
        self,
        *,
        system_prompt: str,
        history: list[tuple[str, str]],
        question: str,
        context_blocks: list[ContextBlock],
    ) -> AsyncIterator[str]:
        answer = self._build_answer(question, context_blocks)
        # 공백을 유지한 채 토큰 단위로 흘려보낸다(스트리밍 인터페이스 통일, 결정적).
        for token in answer.splitlines(keepends=True):
            for piece in token.split(" "):
                yield piece + " " if piece else " "


async def acomplete(
    provider: ChatProvider,
    *,
    system_prompt: str,
    question: str,
    history: list[tuple[str, str]] | None = None,
    context_blocks: list[ContextBlock] | None = None,
) -> str:
    """astream 을 소진해 **전체 응답 문자열**을 반환한다(위키 컴파일 2단계 호출용, PRD 3.7.1).

    위키 Ingest 는 스트리밍이 아니라 완성된 분석 JSON·페이지 마크다운이 필요하므로 토큰을
    모아 하나로 합친다. 도구 호출은 여전히 부여하지 않는다(프롬프트 인젝션 방어, PRD 3.7.5).
    """
    parts: list[str] = []
    async for token in provider.astream(
        system_prompt=system_prompt,
        history=history or [],
        question=question,
        context_blocks=context_blocks or [],
    ):
        parts.append(token)
    return "".join(parts)


@dataclass(frozen=True)
class _OpenAIConfig:
    """OpenAI 호환 프로바이더(vllm/openai/upstage) 접속 설정. base_url None → OpenAI 기본."""

    model: str
    api_key: str
    base_url: str | None


def _resolve_openai_config(settings: Settings, provider: str) -> _OpenAIConfig | None:
    """vllm/openai/upstage 접속 설정을 해석한다(순수). 필수값이 없으면 None.

    - model: CHAT_MODEL 미지정 시 프로바이더별 기본(vllm=glm-5.2, upstage=solar-open2).
    - vllm: CHAT_BASE_URL + CHAT_API_KEY 둘 다 필요.
    - openai: CHAT_API_KEY 필요(CHAT_BASE_URL 미지정 시 OpenAI 기본 엔드포인트).
    - upstage: api_key 는 CHAT_API_KEY, 없으면 UPSTAGE_API_KEY 폴백. base_url 은 Upstage 기본.
    """
    model = settings.chat_model or _DEFAULT_CHAT_MODELS.get(provider, "")
    if provider == "vllm":
        if not settings.chat_base_url or not settings.chat_api_key:
            return None
        return _OpenAIConfig(model, settings.chat_api_key, settings.chat_base_url)
    if provider == "openai":
        if not settings.chat_api_key:
            return None
        return _OpenAIConfig(model, settings.chat_api_key, settings.chat_base_url)
    if provider == "upstage":
        api_key = settings.chat_api_key or settings.upstage_api_key
        if not api_key:
            return None
        return _OpenAIConfig(
            model, api_key, settings.chat_base_url or _UPSTAGE_CHAT_BASE_URL
        )
    return None


def get_chat_provider(settings: Settings) -> ChatProvider | None:
    """설정에 따른 챗 프로바이더를 만든다. 만들 수 없으면 None(챗 요청은 503).

    - fake: 항상 생성.
    - vllm/openai/upstage: 필수값(키·base_url)이 있어야 생성. 없으면 None → 명시적 503.
    """
    provider = settings.chat_provider.lower()
    if provider == "fake":
        return FakeChatProvider()
    if provider in {"vllm", "openai", "upstage"}:
        config = _resolve_openai_config(settings, provider)
        if config is None:
            _log.warning("chat_provider_unconfigured", provider=provider)
            return None
        return OpenAICompatChatProvider(
            model=config.model, api_key=config.api_key, base_url=config.base_url
        )
    _log.warning("chat_provider_unknown", provider=settings.chat_provider)
    return None
