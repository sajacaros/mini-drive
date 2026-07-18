"""upload_sessions 테이블 (PRD 3.2 재개 가능 업로드).

S3 Multipart Upload 기반 재개 업로드의 세션 상태를 담는다. Redis 일회성 티켓(60초)과 달리
업로드 세션은 대용량 파일이 여러 요청에 걸쳐 수 분~수 시간 이어질 수 있고, 고아 multipart
정리를 위해 만료 세션을 열거해야 하므로 DB 에 둔다(진실 소스는 여전히 PostgreSQL).

업로드된 파트의 etag/크기는 여기 저장하지 않는다 — 스테이징된 파트의 진실 소스는 MinIO
(`list_parts`)이고, 재개/완료 시 그로부터 재구성한다. 이 행은 세션 메타데이터만 보관한다.

`file_id` 의미 (kind 에 따라 다름):
  - kind='new'    : 완료 시 생성할 files.id 를 미리 선점(nextval)한 값. 아직 files 행은 없다.
  - kind='version': 재업로드 대상인 기존 files.id. object_key 는 그 파일의 현재 원본 키.
따라서 file_id 에는 FK 를 걸지 않는다('new' 는 아직 존재하지 않는 행을 가리키므로).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UploadSession(Base):
    """재개 가능 업로드 세션 (S3 Multipart Upload 진행 상태)."""

    __tablename__ = "upload_sessions"

    # 세션 토큰(불투명, 추측 불가) — URL 경로에 실린다. int file_id 라우트와 혼동되지 않는다.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # 'new' = 새 파일 업로드, 'version' = 기존 파일 재업로드(새 버전).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # kind 에 따른 의미는 모듈 docstring 참조. FK 없음.
    file_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 'new' 일 때 대상 부모 폴더(None=루트). 'version' 은 사용하지 않음.
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # multipart 대상 오브젝트 키 + MinIO 업로드 ID.
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    upload_id: Mapped[str] = mapped_column(String(255), nullable=False)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    part_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 'version' 재업로드의 버전 충돌 감지 기준(None=강제 덮어쓰기).
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("idx_upload_sessions_user", "user_id"),
        Index("idx_upload_sessions_expires", "expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<UploadSession id={self.id!r} kind={self.kind} file_id={self.file_id}>"
