"""wiki_sources / wiki_jobs 테이블 (전사 단일 LLM 위키, wiki-v2 D1~D9, Phase 7-4).

위키 v2 재설계(wiki-v2 재설계 문서)로 스페이스 개념(wiki_spaces, personal/group 스코프)을
제거했다. 핵심 모델이 **권한 경계 = 컴파일 경계**에서 **출판 동의(체크) = 전사 출판**으로
바뀌었다(wiki-v2 D1):

  - 드라이브 파일/폴더에 "위키에 공유" 체크(= wiki_sources 행)를 두면 그 콘텐츠가 **전사 단일
    위키**로 컴파일되고, 컴파일된 위키 페이지는 **모든 로그인 사용자**가 읽는다(권한 서비스
    특례, wiki-v2 D4). 원본은 기존 권한 그대로 보호된다(출처 링크 클릭 시 권한 재검증).
  - 위키 페이지는 별도 테이블이 아니라 시스템 사용자 소유 `Wiki` 폴더(app_settings 의
    wiki_root_folder_id) 하위의 일반 드라이브 파일이다(권한·버전·휴지통 기존 체계 재사용).

설계 (wiki-v2 3장):
  - wiki_sources: PK(file_id). added_by = 파일 소유자(체크한 사람, D1). status 는 인덱싱/컴파일
    진행 반영(queued/indexed/stale/failed). space_id·recursive 는 제거(폴더는 항상 재귀, D2).
  - wiki_jobs: arq(Redis) 실행 큐와 별개로 이력·상태 조회·재시도 판단의 진실 소스. space_id 제거.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 소스 상태 (wiki_sources.status).
SOURCE_QUEUED = "queued"
SOURCE_INDEXED = "indexed"
SOURCE_STALE = "stale"
SOURCE_FAILED = "failed"

# 잡 종류/상태 (wiki_jobs).
JOB_INGEST = "ingest"
JOB_COMPILE = "compile"
JOB_LINT = "lint"
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"


class WikiSource(Base):
    """위키 공유 체크된 소스 (wiki-v2 3장). 파일 또는 폴더. 폴더는 항상 재귀(D2).

    PK 는 file_id 단독 — 전사 단일 위키이므로 소스는 파일별 1행이다. added_by 는 체크한 사람
    (= 파일 소유자, D1)으로, 폴더 소스일 때 컴파일 대상을 added_by 소유 파일로 한정한다(D2).
    """

    __tablename__ = "wiki_sources"

    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=SOURCE_QUEUED
    )
    last_ingested_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<WikiSource file={self.file_id} status={self.status}>"


class WikiJob(Base):
    """비동기 위키 잡 이력 (wiki-v2 3장). arq(Redis) 실행 큐와 별개의 상태 진실 소스."""

    __tablename__ = "wiki_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # ingest/compile/lint
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=JOB_QUEUED
    )
    retries: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # 이력은 id 역순(최근순) 조회뿐이라 PK 인덱스로 충분하다(별도 보조 인덱스 불요).

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<WikiJob id={self.id} kind={self.kind} status={self.status}>"
