"""file_chunks 테이블 (RAG 인덱스, PRD 5.13, Phase 7-1).

드라이브 파일(위키 페이지 포함)의 현재 버전을 텍스트로 추출·청킹·임베딩한 결과를 담는
벡터 인덱스다. 검색(7-2)은 이 테이블을 `files` 조인으로 권한 사전 필터한 뒤 스캔한다.

설계 (PRD 5.13):
  - 권한 정보를 비정규화하지 않는다 — 조회 시 `files` 조인 + `get_access_level` 로 판정한다
    (5.7 상속 판정과 동일 철학이라 권한 변경이 즉시 반영된다).
  - `embedding` 은 solar-embedding-1-large 기준 4096차원 고정(PRD 3.7.4 — 배포 시 고정, 교체
    시 전체 재임베딩). pgvector HNSW 는 2000차원 제한이 있어 4096차원은 **인덱스 없이 정확
    검색**한다(사전 필터로 후보가 좁혀지는 사내 규모 전제).
  - `version` 은 인덱싱된 파일 버전으로, 버전 갱신 시 stale 판정 기준이다. 새 버전 인덱싱
    성공 시 이전 버전 청크는 삭제한다.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DDL,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 임베딩 차원 — 배포 시 고정(PRD 3.7.4). solar-embedding-1-large = 4096.
EMBEDDING_DIM = 4096

# pgvector 확장을 create_all(통합 테스트) 시 자동 보장한다. 마이그레이션(0004)도 동일 DDL 을
# 실행하지만, 통합 테스트는 alembic 을 거치지 않고 `Base.metadata.create_all` 로 file_chunks 를
# 만들므로 vector 타입이 먼저 존재해야 한다. before_create 는 CREATE TABLE 들보다 앞서 1회 실행된다.
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS vector"),  # type: ignore[no-untyped-call]
)


class FileChunk(Base):
    """파일 한 버전의 청크 하나 + 임베딩 (PRD 5.13)."""

    __tablename__ = "file_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    # 인덱싱된 파일 버전. 버전 갱신 시 stale 판정 기준(PRD 5.13).
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "file_id", "version", "chunk_index", name="uq_file_chunks_file_version_index"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<FileChunk file_id={self.file_id} v={self.version} "
            f"idx={self.chunk_index}>"
        )
