"""임베딩 프로바이더 추상화 (PRD 3.7.4, Phase 7-1).

임베딩 차원은 배포 시 4096 으로 고정한다(PRD 3.7.4 — 교체 시 전체 재임베딩). 프로바이더는
환경변수 `EMBEDDING_PROVIDER` 로 고른다:

  - `upstage` (기본): solar-embedding-1-large, langchain-upstage `UpstageEmbeddings`. 외부 API
    호출이므로 `UPSTAGE_API_KEY` 가 필요하다. 키가 없으면 프로바이더가 생성되지 않고(None),
    인덱싱 잡은 경고 로그 후 스킵한다(fail-open — 파일 서비스 본연 기능에 영향 금지).
  - `fake`: 내용 해시 기반 **결정적** 4096차원 단위벡터. 외부 호출이 전혀 없어 테스트·키 없는
    개발 환경에서 파이프라인 전체를 검증할 수 있다. 같은 내용은 항상 같은 벡터를 낸다.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
from typing import Protocol

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.file_chunk import EMBEDDING_DIM

_log = get_logger("app.embeddings")


class EmbeddingProvider(Protocol):
    """임베딩 프로바이더 인터페이스. 여러 텍스트를 한 번에 벡터로 변환한다."""

    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """문서 청크 목록을 임베딩 벡터 목록으로 변환한다(순서 보존)."""
        ...


def _normalize(vec: list[float]) -> list[float]:
    """L2 정규화(단위벡터). 코사인 유사도 검색과 궁합이 좋고, 0벡터는 그대로 둔다."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


class FakeEmbeddingProvider:
    """내용 해시 기반 결정적 임베딩(외부 호출 없음). 테스트·키 없는 개발용.

    같은 내용 → 같은 4096차원 단위벡터. sha256 을 블록 카운터와 함께 반복 해싱해 필요한
    바이트를 만들고, 4바이트씩 float 로 해석한 뒤 L2 정규화한다. 순수 함수라 결정적이다.
    """

    dim = EMBEDDING_DIM

    def _embed_one(self, text: str) -> list[float]:
        needed = self.dim * 4  # float 당 4바이트
        buf = bytearray()
        counter = 0
        seed = text.encode("utf-8")
        while len(buf) < needed:
            block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            buf.extend(block)
            counter += 1
        floats = [
            struct.unpack_from(">i", buf, i * 4)[0] / 0x7FFFFFFF
            for i in range(self.dim)
        ]
        return _normalize(floats)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class UpstageEmbeddingProvider:
    """Upstage solar-embedding-1-large 래퍼(4096차원). langchain-upstage 를 지연 임포트한다.

    `UpstageEmbeddings.embed_documents` 는 동기 HTTP 호출이므로 `asyncio.to_thread` 로 감싼다.
    """

    dim = EMBEDDING_DIM

    def __init__(self, api_key: str, model: str) -> None:
        # 지연 임포트: fake 경로/유닛 테스트가 langchain-upstage 를 요구하지 않게 한다.
        from langchain_upstage import UpstageEmbeddings  # type: ignore[import-not-found]

        self._client = UpstageEmbeddings(api_key=api_key, model=model)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._client.embed_documents, texts)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    """설정에 따른 임베딩 프로바이더를 만든다. 만들 수 없으면 None(잡이 스킵).

    - fake: 항상 생성.
    - upstage: 키가 있으면 생성, 없으면 None(fail-open, 경고는 호출자가 남긴다).
    """
    provider = settings.embedding_provider.lower()
    if provider == "fake":
        return FakeEmbeddingProvider()
    if provider == "upstage":
        if not settings.upstage_api_key:
            return None
        return UpstageEmbeddingProvider(
            settings.upstage_api_key, settings.upstage_embedding_model
        )
    _log.warning("embedding_provider_unknown", provider=settings.embedding_provider)
    return None
