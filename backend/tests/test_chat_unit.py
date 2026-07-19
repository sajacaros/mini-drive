"""챗봇 순수 로직 단위 테스트 (DB/외부 API 불필요) — PRD 3.7.3/3.7.5, Phase 7-2.

검증 축: citations 구조 산출 / 시스템 프롬프트 인젝션 방어 / fake 챗 결정성 / SSE 프레임 포맷 /
벡터 리터럴 / 사후 검증 탈락 폐기(get_access_level 모킹).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.api.routes.chat import _sse
from app.models import File, User
from app.models.enums import GroupPermission
from app.services import retrieval as retrieval_service
from app.services.chat_llm import FakeChatProvider, _resolve_openai_config
from app.services.chat_pipeline import build_citations, build_system_prompt
from app.services.retrieval import RetrievedChunk, _vector_literal


def _chunk(chunk_id: int, file_id: int, name: str, content: str, dist: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        file_id=file_id,
        file_name=name,
        version=1,
        content=content,
        distance=dist,
    )


class TestCitations:
    def test_structure_from_chunks(self) -> None:
        chunks = [_chunk(10, 5, "a.txt", "내용 " * 200, 0.1)]
        cites = build_citations(chunks)
        assert cites == [
            {
                "file_id": 5,
                "file_name": "a.txt",
                "chunk_id": 10,
                "version": 1,
                "snippet": chunks[0].content[:200],
            }
        ]
        # snippet 은 200자 이하로 잘린다.
        assert len(cites[0]["snippet"]) <= 200

    def test_empty(self) -> None:
        assert build_citations([]) == []


class TestSystemPrompt:
    def test_injection_defense_and_delimiters(self) -> None:
        prompt = build_system_prompt([_chunk(1, 1, "report.txt", "본문 내용", 0.2)])
        # 컨텍스트를 데이터로만 취급하라는 인젝션 방어 지시가 있어야 한다.
        assert "따르지 마" in prompt
        # 명확한 구분자 + 파일명/버전 메타가 포함된다.
        assert "<컨텍스트>" in prompt and "</컨텍스트>" in prompt
        assert 'report.txt' in prompt
        assert "본문 내용" in prompt

    def test_no_context(self) -> None:
        prompt = build_system_prompt([])
        assert "<컨텍스트>" in prompt
        assert "찾지 못했" in prompt


class TestFakeChat:
    def _run(self, provider: FakeChatProvider, blocks: list[tuple[str, str]]) -> list[str]:
        async def collect() -> list[str]:
            out = []
            async for tok in provider.astream(
                system_prompt="sys",
                history=[],
                question="질문?",
                context_blocks=blocks,
            ):
                out.append(tok)
            return out

        return asyncio.run(collect())

    def test_deterministic(self) -> None:
        p = FakeChatProvider()
        blocks = [("a.txt", "가나다 라마바"), ("b.txt", "사아자")]
        first = self._run(p, blocks)
        second = self._run(p, blocks)
        assert first == second, "같은 입력은 같은 토큰 스트림을 내야 한다(결정적)"

    def test_cites_file_names(self) -> None:
        p = FakeChatProvider()
        answer = "".join(self._run(p, [("report.txt", "매출 요약 내용"), ("plan.txt", "계획")]))
        assert "다음 문서에서 찾은 내용입니다" in answer
        assert "[report.txt]" in answer
        assert "[plan.txt]" in answer

    def test_no_context_message(self) -> None:
        p = FakeChatProvider()
        answer = "".join(self._run(p, []))
        assert "찾지 못했" in answer

    def test_streams_multiple_tokens(self) -> None:
        p = FakeChatProvider()
        tokens = self._run(p, [("a.txt", "내용")])
        assert len(tokens) > 1, "토큰 단위로 스트리밍해야 한다"


def _settings(**kw) -> SimpleNamespace:
    base = dict(chat_model=None, chat_base_url=None, chat_api_key=None, upstage_api_key=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestProviderResolution:
    """CHAT_PROVIDER=vllm/openai/upstage 접속 설정 해석(_resolve_openai_config)."""

    def test_upstage_uses_upstage_key_fallback_and_defaults(self) -> None:
        cfg = _resolve_openai_config(
            _settings(upstage_api_key="up_key"), "upstage"
        )
        assert cfg is not None
        assert cfg.api_key == "up_key", "CHAT_API_KEY 없으면 UPSTAGE_API_KEY 폴백"
        assert cfg.base_url == "https://api.upstage.ai/v1"
        assert cfg.model == "solar-open2", "CHAT_MODEL 미지정 시 solar-open2"

    def test_upstage_prefers_chat_api_key(self) -> None:
        cfg = _resolve_openai_config(
            _settings(chat_api_key="chat_key", upstage_api_key="up_key"), "upstage"
        )
        assert cfg is not None and cfg.api_key == "chat_key"

    def test_upstage_model_and_base_url_override(self) -> None:
        cfg = _resolve_openai_config(
            _settings(upstage_api_key="up_key", chat_model="solar-pro",
                      chat_base_url="https://alt.example/v1"),
            "upstage",
        )
        assert cfg is not None
        assert cfg.model == "solar-pro"
        assert cfg.base_url == "https://alt.example/v1"

    def test_upstage_no_key_returns_none(self) -> None:
        assert _resolve_openai_config(_settings(), "upstage") is None

    def test_vllm_requires_base_url_and_key(self) -> None:
        assert _resolve_openai_config(_settings(chat_api_key="k"), "vllm") is None
        cfg = _resolve_openai_config(
            _settings(chat_api_key="k", chat_base_url="http://vllm:8000/v1"), "vllm"
        )
        assert cfg is not None and cfg.model == "glm-5.2" and cfg.base_url == "http://vllm:8000/v1"

    def test_openai_defaults(self) -> None:
        assert _resolve_openai_config(_settings(), "openai") is None
        cfg = _resolve_openai_config(_settings(chat_api_key="k"), "openai")
        assert cfg is not None and cfg.model == "gpt-4o-mini" and cfg.base_url is None


class TestVectorLiteral:
    def test_format(self) -> None:
        lit = _vector_literal([1.0, 2.5, -3.0])
        assert lit.startswith("[") and lit.endswith("]")
        parts = lit[1:-1].split(",")
        assert len(parts) == 3
        assert float(parts[0]) == 1.0 and float(parts[2]) == -3.0


class TestSseFrame:
    def test_token_frame(self) -> None:
        frame = _sse("token", {"value": "안녕"})
        assert frame == 'event: token\ndata: {"value": "안녕"}\n\n'

    def test_citations_frame_is_json_array(self) -> None:
        frame = _sse("citations", [{"file_id": 1}])
        lines = frame.strip().split("\n")
        assert lines[0] == "event: citations"
        payload = json.loads(lines[1][len("data: ") :])
        assert payload == [{"file_id": 1}]


class _FakeSession:
    """retrieve() 사후 검증용 최소 세션 — get(File, id) 만 지원."""

    def __init__(self, files: dict[int, File]) -> None:
        self._files = files

    async def get(self, model: type, pk: int):  # noqa: ANN001 - 테스트 스텁
        return self._files.get(pk)


class TestPostVerify:
    """사후 검증 — 소유/그룹 통과, 비권한 파일 청크는 폐기(get_access_level 모킹)."""

    def test_drops_unauthorized(self, monkeypatch) -> None:
        user = User(id=100, email="u@x.com", password_hash="x")
        # file 1: 내 소유(owner) / file 2: 타인·권한 없음 / file 3: 타인·그룹 read
        files = {
            1: File(id=1, user_id=100, name="mine.txt", file_key="k1", size=1),
            2: File(id=2, user_id=200, name="secret.txt", file_key="k2", size=1),
            3: File(id=3, user_id=200, name="shared.txt", file_key="k3", size=1),
        }
        fake_session = _FakeSession(files)

        async def fake_coarse(session, u, vec, limit, space_ids=None):  # noqa: ANN001
            return [
                _chunk(11, 1, "mine.txt", "내 문서", 0.1),
                _chunk(22, 2, "secret.txt", "비밀 문서", 0.2),
                _chunk(33, 3, "shared.txt", "공유 문서", 0.3),
            ]

        async def fake_access(session, u, file):  # noqa: ANN001
            return GroupPermission.READ if file.id == 3 else None

        monkeypatch.setattr(retrieval_service, "_coarse_search", fake_coarse)
        monkeypatch.setattr(retrieval_service, "get_access_level", fake_access)

        result = asyncio.run(
            retrieval_service.retrieve(fake_session, user, [0.0] * 4, k=8)
        )
        passed_ids = {c.file_id for c in result}
        assert passed_ids == {1, 3}, "소유·그룹 파일만 통과, 비권한 파일은 폐기"

    def test_respects_k_limit(self, monkeypatch) -> None:
        user = User(id=100, email="u@x.com", password_hash="x")
        files = {i: File(id=i, user_id=100, name=f"f{i}.txt", file_key=f"k{i}", size=1)
                 for i in range(1, 6)}
        fake_session = _FakeSession(files)

        async def fake_coarse(session, u, vec, limit, space_ids=None):  # noqa: ANN001
            return [_chunk(i * 10, i, f"f{i}.txt", "c", i * 0.1) for i in range(1, 6)]

        monkeypatch.setattr(retrieval_service, "_coarse_search", fake_coarse)
        result = asyncio.run(
            retrieval_service.retrieve(fake_session, user, [0.0] * 4, k=2)
        )
        assert len(result) == 2
        # 거리 오름차순 유지(가장 가까운 것 우선).
        assert [c.file_id for c in result] == [1, 2]
