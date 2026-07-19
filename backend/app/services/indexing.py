"""RAG 인덱싱 파이프라인 (PRD 3.7, Phase 7-1).

드라이브 파일을 텍스트로 추출 → 청킹 → 임베딩 → `file_chunks` 에 저장한다. arq 워커
(app.worker)가 업로드/버전/삭제 훅으로 큐잉된 잡을 이 서비스로 처리한다. 검색(7-2)은 만들지
않는다 — 여기서는 인덱스를 채우고 비우는 것까지만 담당한다.

설계 결정:
  - **eligibility(적격성)**: (a) 파일이며 삭제 상태가 아니고 (b) 자신·조상 폴더 어디에도
    indexing_excluded 가 없고(권한 상속과 동일한 recursive CTE 판정) (c) 지원 형식이며
    (d) 크기 상한 이하일 때만 인덱싱한다. 순수 판정부(decide_eligibility)는 DB 없이 단위
    테스트 가능하도록 분리한다.
  - **멱등성**: 같은 (file_id, version) 재실행 시 기존 청크를 삭제 후 재삽입한다. 새 버전
    인덱싱이 성공하면 이전 버전 청크는 삭제한다(stale 제거, PRD 5.13 version 기준).
  - **fail-open**: 임베딩 프로바이더가 없으면(예: upstage 키 없음) 경고 후 스킵한다. 파일
    서비스 본연 기능(업로드/다운로드)에는 절대 영향을 주지 않는다.
  - 폴더 대상 잡은 하위 파일로 팬아웃한다(인덱싱 제외 플래그 토글/휴지통이 폴더에 걸리므로).
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import File, FileChunk
from app.services import previews as previews_service
from app.services.embeddings import EmbeddingProvider
from app.services.storage import StorageService

_log = get_logger("app.indexing")

# Upstage Document Parse 로 처리하는 바이너리 문서(직접 디코드 불가). 그 외 텍스트 계열은
# previews.classify == "text" 로 판정해 직접 디코드한다.
_DOCUMENT_PARSE_MIMES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.hancom.hwp",
    "application/x-hwp",
}

# 대상 파일과 모든 하위 항목을 재귀로 훑는 공통 CTE (files.py 와 동일 패턴).
_SUBTREE_CTE = (
    "WITH RECURSIVE sub AS ("
    "  SELECT id FROM files WHERE id = :root "
    "  UNION ALL "
    "  SELECT f.id FROM files f JOIN sub s ON f.parent_folder_id = s.id"
    ")"
)


# --- 형식 판정 (순수) --------------------------------------------------------


def _normalize_mime(mime: str | None) -> str:
    if not mime:
        return ""
    return mime.split(";", 1)[0].strip().lower()


def is_text_extractable(mime: str | None) -> bool:
    """직접 디코드 가능한 텍스트 계열인지(text/*, json, csv, xml, yaml, code 등)."""
    return previews_service.classify(mime) == "text"


def needs_document_parse(mime: str | None) -> bool:
    """Upstage Document Parse 가 필요한 바이너리 문서(PDF/오피스/HWP)인지."""
    return _normalize_mime(mime) in _DOCUMENT_PARSE_MIMES


# --- eligibility 순수 판정 (DB 무관, 단위 테스트 대상) ------------------------


@dataclass(frozen=True)
class EligibilityDecision:
    """인덱싱 적격성 판정 결과.

    method: "text"(직접 디코드) | "parse"(Document Parse) | None(부적격).
    reason: "ok" | "not_a_file" | "deleted" | "excluded" | "too_large"
            | "unsupported_type" | "parse_unavailable".
    """

    eligible: bool
    reason: str
    method: str | None = None


def decide_eligibility(
    *,
    is_folder: bool,
    is_deleted: bool,
    mime: str | None,
    size: int,
    excluded: bool,
    max_size: int,
    parse_available: bool,
) -> EligibilityDecision:
    """DB 무관 순수 판정 — 폴더/삭제/제외/크기/형식으로 인덱싱 가능 여부를 정한다.

    parse_available 은 Upstage Document Parse 사용 가능 여부(UPSTAGE_API_KEY 존재)다.
    """
    if is_folder:
        return EligibilityDecision(False, "not_a_file")
    if is_deleted:
        return EligibilityDecision(False, "deleted")
    if excluded:
        return EligibilityDecision(False, "excluded")
    if size > max_size:
        return EligibilityDecision(False, "too_large")
    if is_text_extractable(mime):
        return EligibilityDecision(True, "ok", "text")
    if needs_document_parse(mime):
        if not parse_available:
            return EligibilityDecision(False, "parse_unavailable")
        return EligibilityDecision(True, "ok", "parse")
    return EligibilityDecision(False, "unsupported_type")


def ancestor_excluded(flags: list[bool]) -> bool:
    """자신·조상들의 indexing_excluded 플래그 중 하나라도 True 면 제외 대상이다."""
    return any(flags)


# --- eligibility DB 판정 -----------------------------------------------------

_ANCESTOR_EXCLUDED_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id, indexing_excluded
        FROM files WHERE id = :file_id
        UNION ALL
        SELECT f.id, f.parent_folder_id, f.indexing_excluded
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT COALESCE(bool_or(indexing_excluded), FALSE) FROM ancestors
    """
)


async def is_excluded(session: AsyncSession, file_id: int) -> bool:
    """자신 또는 조상 폴더 어디에든 indexing_excluded=TRUE 가 있으면 True (조회 시 판정)."""
    result = await session.execute(_ANCESTOR_EXCLUDED_SQL, {"file_id": file_id})
    return bool(result.scalar_one())


async def check_eligibility(
    session: AsyncSession, file: File, settings: Settings
) -> EligibilityDecision:
    """DB 조회(조상 제외 판정)를 포함한 적격성 판정."""
    excluded = await is_excluded(session, file.id)
    return decide_eligibility(
        is_folder=file.is_folder,
        is_deleted=file.is_deleted,
        mime=file.mime_type,
        size=file.size,
        excluded=excluded,
        max_size=settings.indexing_max_file_size,
        parse_available=bool(settings.upstage_api_key),
    )


# --- 텍스트 추출 / 청킹 ------------------------------------------------------


def _extract_text_direct(data: bytes) -> str:
    """텍스트 계열 바이트를 UTF-8 로 디코드한다(디코드 불가 바이트는 대체 문자로)."""
    return data.decode("utf-8", errors="replace")


def _extract_text_document_parse(data: bytes, settings: Settings) -> str:
    """Upstage Document Parse 로 바이너리 문서에서 텍스트를 추출한다(런타임에 키가 있을 때만).

    UpstageDocumentParseLoader 는 파일 경로를 받으므로 임시 파일에 쓴다. 지연 임포트로
    fake/유닛 경로가 langchain-upstage 를 요구하지 않게 한다.
    """
    import tempfile

    from langchain_upstage import UpstageDocumentParseLoader  # type: ignore[import-not-found]

    with tempfile.NamedTemporaryFile(suffix=".bin") as tmp:
        tmp.write(data)
        tmp.flush()
        loader = UpstageDocumentParseLoader(
            tmp.name, api_key=settings.upstage_api_key, split="none"
        )
        docs = loader.load()
    return "\n\n".join(d.page_content for d in docs if d.page_content)


def chunk_text(text_content: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """RecursiveCharacterTextSplitter 로 청크를 나눈다. 공백만 있는 청크는 버린다."""
    if not text_content or not text_content.strip():
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )
    return [c for c in splitter.split_text(text_content) if c.strip()]


def _approx_token_count(chunk: str) -> int:
    """토큰 수 근사(공백 분할). 정확한 토크나이저 없이 운영 지표용 대략값."""
    return len(chunk.split())


# --- 청크 저장 (멱등 upsert) -------------------------------------------------


async def _replace_chunks(
    session: AsyncSession,
    file_id: int,
    version: int,
    chunks: list[str],
    embeddings: list[list[float]],
) -> None:
    """(file_id, version) 청크를 통째로 교체하고 이전 버전 청크를 삭제한다(멱등).

    같은 버전 재실행 시 기존 청크 삭제 후 재삽입하므로 중복이 생기지 않는다. 성공적으로
    새 버전을 심으면 다른 버전의 청크(stale)를 함께 제거한다(PRD 5.13 version 기준).
    """
    # 1) 같은 버전 기존 청크 삭제 (재실행 멱등성).
    await session.execute(
        delete(FileChunk).where(
            FileChunk.file_id == file_id, FileChunk.version == version
        )
    )
    # 2) 새 청크 삽입.
    for idx, (content, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
        session.add(
            FileChunk(
                file_id=file_id,
                version=version,
                chunk_index=idx,
                content=content,
                embedding=embedding,
                token_count=_approx_token_count(content),
            )
        )
    # 3) 이전/다른 버전 청크 제거 (stale). 현재 버전만 남긴다.
    await session.execute(
        delete(FileChunk).where(
            FileChunk.file_id == file_id, FileChunk.version != version
        )
    )
    await session.commit()


# --- 인덱싱 진입점 -----------------------------------------------------------


@dataclass(frozen=True)
class IndexResult:
    """인덱싱 결과. status: "indexed" | "empty" | "skipped". chunk_count 는 저장된 청크 수."""

    status: str
    reason: str
    chunk_count: int = 0


async def index_file(
    session: AsyncSession,
    storage: StorageService,
    provider: EmbeddingProvider | None,
    file_id: int,
    settings: Settings,
) -> IndexResult:
    """단일 파일을 인덱싱한다(적격성 판정 → 추출 → 청킹 → 임베딩 → 멱등 저장).

    부적격(삭제·제외·미지원·초과)이면 남아 있을 수 있는 청크를 정리하고 스킵한다. 임베딩
    프로바이더가 없으면(fail-open) 청크는 건드리지 않고 스킵한다.
    """
    file = await session.get(File, file_id)
    if file is None:
        return IndexResult("skipped", "not_found")

    decision = await check_eligibility(session, file, settings)
    if not decision.eligible:
        # 부적격이면 stale 청크가 남지 않도록 정리한다(재업로드로 형식 변경·제외 전환 등).
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
        await session.commit()
        _log.info("index_skip", file_id=file_id, reason=decision.reason)
        return IndexResult("skipped", decision.reason)

    if provider is None:
        # fail-open — 프로바이더 부재(예: upstage 키 없음)는 파일 서비스에 영향 주지 않는다.
        _log.warning("index_skip_no_provider", file_id=file_id)
        return IndexResult("skipped", "no_provider")

    data = await storage.get_bytes_async(file.file_key)
    if decision.method == "parse":
        content = _extract_text_document_parse(data, settings)
    else:
        content = _extract_text_direct(data)

    chunks = chunk_text(
        content, settings.indexing_chunk_size, settings.indexing_chunk_overlap
    )
    version = file.current_version
    if not chunks:
        await _replace_chunks(session, file_id, version, [], [])
        _log.info("index_empty", file_id=file_id, version=version)
        return IndexResult("empty", "no_chunks")

    embeddings = await provider.embed_documents(chunks)
    await _replace_chunks(session, file_id, version, chunks, embeddings)
    _log.info(
        "index_done", file_id=file_id, version=version, chunks=len(chunks)
    )
    return IndexResult("indexed", "ok", len(chunks))


async def _descendant_file_ids(session: AsyncSession, root_id: int) -> list[int]:
    """대상 폴더 하위(재귀)의 비폴더·미삭제 파일 id 목록."""
    rows = await session.execute(
        text(
            _SUBTREE_CTE
            + " SELECT f.id FROM files f "
            "WHERE f.id IN (SELECT id FROM sub) "
            "AND f.is_folder = FALSE AND f.is_deleted = FALSE"
        ),
        {"root": root_id},
    )
    return [r.id for r in rows]


async def index_target(
    session: AsyncSession,
    storage: StorageService,
    provider: EmbeddingProvider | None,
    file_id: int,
    settings: Settings,
) -> int:
    """파일이면 그 파일을, 폴더면 하위 파일들을 인덱싱한다. 인덱싱한 파일 수를 반환한다.

    폴더 팬아웃은 인덱싱 제외 플래그 토글(false 로 재인덱싱)·휴지통 복원이 폴더에 걸릴 수
    있어 필요하다. 개별 파일 실패는 로그만 남기고 나머지를 계속 처리한다.
    """
    file = await session.get(File, file_id)
    if file is None:
        return 0
    if not file.is_folder:
        await index_file(session, storage, provider, file_id, settings)
        return 1

    ids = await _descendant_file_ids(session, file_id)
    for fid in ids:
        try:
            await index_file(session, storage, provider, fid, settings)
        except Exception as exc:  # noqa: BLE001 - 개별 실패가 나머지를 막지 않게 한다.
            await session.rollback()
            _log.warning("index_child_failed", file_id=fid, error=str(exc))
    return len(ids)


async def drop_index(session: AsyncSession, file_id: int) -> None:
    """파일과 모든 하위 항목의 청크를 삭제한다(휴지통 이동·인덱싱 제외 전환).

    폴더면 하위 재귀. 영구 삭제는 files FK CASCADE 로 자동 정리되므로 이 훅이 필요 없다.
    """
    await session.execute(
        text(
            _SUBTREE_CTE
            + " DELETE FROM file_chunks "
            "WHERE file_id IN (SELECT id FROM sub)"
        ),
        {"root": file_id},
    )
    await session.commit()
