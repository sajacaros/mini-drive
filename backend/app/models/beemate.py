"""beemate_devices / beemate_transfers — 내 기기끼리 파일 보내기 (spec/beemate.md).

드라이브 파일(`files`)과 분리한다. 전송은 거쳐 가는 데이터라 받는 순간 행을 지우고, 할당량·
버전·위키·파일 이벤트 어느 것도 타지 않는다. 상태 컬럼을 두지 않는 것도 같은 이유다 — 행이
있으면 아직 안 받은 것이다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BeemateDevice(Base):
    """사용자의 기기 하나(폰 앱 설치 1건, 또는 PC 브라우저 1개)."""

    __tablename__ = "beemate_devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 'android' | 'ios' | 'web' (schemas.beemate.Platform). native enum 미사용(VARCHAR).
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 이벤트 스트림에 마지막으로 붙은 시각. 온라인 여부는 Redis 키로 따로 판단한다.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("idx_beemate_devices_user", "user_id"),)


class BeemateTransfer(Base):
    """아직 받지 않은 전송 한 건. 받으면(DELETE) 오브젝트와 함께 사라진다."""

    __tablename__ = "beemate_transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    to_device_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("beemate_devices.id", ondelete="CASCADE"), nullable=False
    )
    # 표시용. 보낸 기기를 지워도 전송은 받을 수 있어야 하므로 SET NULL.
    from_device_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("beemate_devices.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # 수신 목록(기기별)과 만료 정리(시각순)가 두 주 조회다.
        Index("idx_beemate_transfers_device", "to_device_id", "created_at"),
        Index("idx_beemate_transfers_created", "created_at"),
    )
