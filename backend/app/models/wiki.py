"""wiki_spaces / wiki_sources / wiki_jobs 테이블 (LLM 위키, PRD 5.14~5.15, Phase 7-3).

Karpathy LLM Wiki 패턴 × 권한 경계. 핵심 불변식은 **권한 경계 = 컴파일 경계**다(PRD 3.7.1):
스페이스 스코프(personal/group)가 read 가능한 소스만 컴파일에 투입되고, 컴파일된 페이지는
스코프 소유 폴더(root_folder_id) 하위의 일반 드라이브 파일로 저장되므로 "페이지를 읽을 수
있는 사람 = 그 소스를 읽을 수 있는 사람"이 구조적으로 보장된다.

설계 (PRD 5.14):
  - 위키 페이지는 별도 테이블이 아니라 root_folder_id 하위의 files 행(권한·버전·휴지통 재사용).
    페이지 frontmatter 의 `locked: true` 는 연쇄 갱신 제외(사람 확정 페이지 보호, 3.7.1 규칙 5).
  - wiki_sources 는 등록 소스(파일 또는 재귀 폴더)와 인덱싱 상태(queued/indexed/stale/failed).
  - wiki_jobs 는 arq(Redis) 실행 큐와 별개로 이력·상태 조회·재시도 판단의 진실 소스(PRD 5.15).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 스코프 값 상수 (wiki_spaces.scope).
SCOPE_PERSONAL = "personal"
SCOPE_GROUP = "group"

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


class WikiSpace(Base):
    """위키 스페이스 (PRD 5.14). personal(user_id) 또는 group(group_id) 스코프.

    root_folder_id 는 위키 페이지(.md 드라이브 파일)가 저장되는 폴더로, 그 폴더의 소유자
    (files.user_id)가 곧 스페이스 생성자다 — 백그라운드 컴파일이 페이지를 쓸 때의 자격.
    """

    __tablename__ = "wiki_spaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id"), nullable=True
    )
    root_folder_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id"), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "(scope = 'personal' AND user_id IS NOT NULL AND group_id IS NULL)"
            " OR (scope = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
            name="ck_wiki_spaces_scope",
        ),
        Index("idx_wiki_spaces_user", "user_id"),
        Index("idx_wiki_spaces_group", "group_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<WikiSpace id={self.id} scope={self.scope} name={self.name!r}>"


class WikiSource(Base):
    """스페이스 소스 등록 (PRD 5.14). 파일 또는 재귀 폴더. 상태는 인덱싱/컴파일 진행 반영."""

    __tablename__ = "wiki_sources"

    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wiki_spaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("files.id", ondelete="CASCADE"),
        primary_key=True,
    )
    recursive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
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
        return (
            f"<WikiSource space={self.space_id} file={self.file_id} "
            f"status={self.status}>"
        )


class WikiJob(Base):
    """비동기 위키 잡 이력 (PRD 5.15). arq(Redis) 실행 큐와 별개의 상태 진실 소스."""

    __tablename__ = "wiki_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wiki_spaces.id", ondelete="CASCADE"),
        nullable=False,
    )
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

    __table_args__ = (Index("idx_wiki_jobs_space", "space_id", "id"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<WikiJob id={self.id} space={self.space_id} kind={self.kind} "
            f"status={self.status}>"
        )
