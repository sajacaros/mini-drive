"""beemate_devices / beemate_transfers — 내 기기끼리 파일 보내기 (spec/beemate.md).

드라이브 파일(`files`)과 분리한다. 전송은 거쳐 가는 데이터라 받는 순간 행을 지우고, 할당량·
버전·위키·파일 이벤트 어느 것도 타지 않는다. 상태 컬럼을 두지 않는 것도 같은 이유다 — 행이
있으면 아직 안 받은 것이다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BeemateDevice(Base):
    """사용자의 기기 하나(폰 앱 설치 1건, 또는 PC 브라우저 1개).

    등록 화면이 없다 — 클라이언트가 처음 붙을 때 스스로 만든 client_id 로 서버가 찾아 쓰거나
    만든다(upsert). 새로고침·재연결에도 같은 기기로 남게 하는 이름표가 client_id 다.
    """

    __tablename__ = "beemate_devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 클라이언트가 만든 고유 id(브라우저 localStorage, 앱 설치별). 사용자 안에서 유일하다.
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 'android' | 'ios' | 'web' (schemas.beemate.Platform). native enum 미사용(VARCHAR).
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    # 즐겨찾기 — 켜져 있든 꺼져 있든 목록 맨 위.
    is_favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 숨김 — 보낼 곳 목록에서 뺀다(관리 목록에는 남는다).
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 이벤트 스트림에 마지막으로 붙은 시각. 온라인 여부는 Redis 키로 따로 판단한다.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uq_beemate_devices_client", "user_id", "client_id", unique=True),
    )


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
