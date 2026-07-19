"""RAG 인덱싱 순수 로직 단위 테스트 (DB/외부 API 불필요) — PRD 3.7, Phase 7-1.

검증 축: eligibility 판정(폴더/삭제/제외/크기/형식) / 조상 제외 상속 / MIME 판정 /
청킹(빈 텍스트·경계·오버랩) / fake 임베딩(결정성·차원·정규화).
"""

from __future__ import annotations

import asyncio
import math

from app.models.file_chunk import EMBEDDING_DIM
from app.services.embeddings import FakeEmbeddingProvider
from app.services.indexing import (
    ancestor_excluded,
    chunk_text,
    decide_eligibility,
    is_text_extractable,
    needs_document_parse,
)

MAX = 20 * 1024 * 1024


def _decide(**kw):
    base = dict(
        is_folder=False,
        is_deleted=False,
        mime="text/plain",
        size=100,
        excluded=False,
        max_size=MAX,
        parse_available=True,
    )
    base.update(kw)
    return decide_eligibility(**base)


class TestEligibility:
    def test_text_file_eligible_text_method(self) -> None:
        d = _decide(mime="text/markdown")
        assert d.eligible and d.method == "text" and d.reason == "ok"

    def test_json_and_code_are_text(self) -> None:
        assert _decide(mime="application/json").method == "text"
        assert _decide(mime="application/javascript").method == "text"

    def test_folder_not_eligible(self) -> None:
        d = _decide(is_folder=True)
        assert not d.eligible and d.reason == "not_a_file"

    def test_deleted_not_eligible(self) -> None:
        assert _decide(is_deleted=True).reason == "deleted"

    def test_excluded_not_eligible(self) -> None:
        assert _decide(excluded=True).reason == "excluded"

    def test_too_large_not_eligible(self) -> None:
        assert _decide(size=MAX + 1).reason == "too_large"

    def test_pdf_needs_parse_available(self) -> None:
        d = _decide(mime="application/pdf", parse_available=True)
        assert d.eligible and d.method == "parse"

    def test_pdf_parse_unavailable_skips(self) -> None:
        d = _decide(mime="application/pdf", parse_available=False)
        assert not d.eligible and d.reason == "parse_unavailable"

    def test_office_docx_needs_parse(self) -> None:
        docx = (
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        )
        assert _decide(mime=docx, parse_available=True).method == "parse"

    def test_binary_unsupported(self) -> None:
        d = _decide(mime="application/zip")
        assert not d.eligible and d.reason == "unsupported_type"

    def test_none_mime_unsupported(self) -> None:
        assert not _decide(mime=None).eligible

    def test_priority_folder_before_excluded(self) -> None:
        # 폴더 판정이 제외/삭제보다 앞선다(폴더는 애초에 파일이 아님).
        assert _decide(is_folder=True, excluded=True).reason == "not_a_file"


class TestAncestorExclusion:
    def test_no_flags_not_excluded(self) -> None:
        assert ancestor_excluded([False, False, False]) is False

    def test_any_ancestor_flag_excludes(self) -> None:
        # 조상 폴더 하나라도 제외면 하위 전체 제외 (권한 상속과 동일 철학).
        assert ancestor_excluded([False, True, False]) is True

    def test_self_flag_excludes(self) -> None:
        assert ancestor_excluded([True]) is True

    def test_empty_not_excluded(self) -> None:
        assert ancestor_excluded([]) is False


class TestMimeJudgment:
    def test_text_extractable(self) -> None:
        assert is_text_extractable("text/plain")
        assert is_text_extractable("text/x-python; charset=utf-8")
        assert is_text_extractable("application/json")
        assert not is_text_extractable("application/pdf")
        assert not is_text_extractable(None)

    def test_document_parse(self) -> None:
        assert needs_document_parse("application/pdf")
        assert needs_document_parse("application/pdf; charset=binary")
        assert not needs_document_parse("text/plain")
        assert not needs_document_parse(None)


class TestChunking:
    def test_empty_text_no_chunks(self) -> None:
        assert chunk_text("", 1000, 150) == []
        assert chunk_text("   \n  ", 1000, 150) == []

    def test_short_text_single_chunk(self) -> None:
        chunks = chunk_text("hello world", 1000, 150)
        assert chunks == ["hello world"]

    def test_long_text_multiple_chunks(self) -> None:
        text = "\n".join(f"line {i} some content here" for i in range(200))
        chunks = chunk_text(text, 100, 20)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)
        # 원문 토큰이 보존되는지(첫/끝 라인이 어딘가에 존재).
        joined = " ".join(chunks)
        assert "line 0" in joined and "line 199" in joined

    def test_whitespace_only_chunks_dropped(self) -> None:
        chunks = chunk_text("aaaa", 2, 0)
        assert all(c.strip() for c in chunks)


class TestFakeEmbedding:
    def test_dim_is_4096(self) -> None:
        fp = FakeEmbeddingProvider()
        assert fp.dim == EMBEDDING_DIM == 4096
        vec = asyncio.run(fp.embed_documents(["x"]))[0]
        assert len(vec) == 4096

    def test_deterministic(self) -> None:
        fp = FakeEmbeddingProvider()
        a = asyncio.run(fp.embed_documents(["deterministic content"]))[0]
        b = asyncio.run(fp.embed_documents(["deterministic content"]))[0]
        assert a == b

    def test_different_text_different_vector(self) -> None:
        fp = FakeEmbeddingProvider()
        vecs = asyncio.run(fp.embed_documents(["alpha", "beta"]))
        assert vecs[0] != vecs[1]

    def test_normalized_unit_vector(self) -> None:
        fp = FakeEmbeddingProvider()
        vec = asyncio.run(fp.embed_documents(["normalize me"]))[0]
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_batch_order_preserved(self) -> None:
        fp = FakeEmbeddingProvider()
        texts = ["one", "two", "three"]
        batch = asyncio.run(fp.embed_documents(texts))
        individual = [asyncio.run(fp.embed_documents([t]))[0] for t in texts]
        assert batch == individual
