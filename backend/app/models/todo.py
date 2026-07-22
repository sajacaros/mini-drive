"""routines / todo_items 테이블 — 데일리 투두 + 반복 루틴.

설계:
  - Routine 은 "매일 운동" 같은 반복 습관 템플릿이다. 스케줄러(크론) 없이, 사용자가 특정
    날짜의 투두를 조회하는 시점에 서비스가 그 날짜에 해당하는 루틴 항목을 TodoItem 으로
    물질화(materialize)한다. 자정이 지나 새 날짜를 열면 루틴 항목이 자동으로 생겨 보인다.
  - TodoItem 은 특정 날짜(todo_date)에 속한 할 일 한 건이다. routine_id 가 있으면 루틴에서
    파생된 것, NULL 이면 그날 직접 추가한 임시 항목이다.
  - (user_id, todo_date, routine_id) 부분 유니크 인덱스로 같은 루틴이 같은 날 중복
    물질화되는 것을 막는다(routine_id IS NOT NULL 인 행만 대상).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Routine(Base):
    """반복 루틴 템플릿 (매일/특정 요일/매월 특정일). 사용자별로 소유된다.

    비활성화(is_active=False)하면 이후 날짜에는 더 이상 물질화되지 않지만, 이미 만들어진
    과거 TodoItem 은 이력으로 남는다. 삭제 시 파생 TodoItem 의 routine_id 는 SET NULL 되어
    임시 항목으로 남고 과거 기록/리포트가 보존된다.
    """

    __tablename__ = "routines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # 'daily' | 'weekly' | 'monthly' (models.enums.RoutineFrequency). native enum 미사용(VARCHAR).
    frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'daily'")
    )
    # weekly 일 때만 의미 — "0,1,2" 형태(월=0..일=6) 콤마 구분. 그 외 주기면 NULL.
    days_of_week: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # monthly 일 때만 의미 — 1~31. 그 외 주기면 NULL.
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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

    owner: Mapped[User] = relationship("User", foreign_keys=[user_id])

    __table_args__ = (Index("idx_routines_user", "user_id"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<Routine id={self.id} title={self.title!r} freq={self.frequency}>"


class TodoItem(Base):
    """특정 날짜의 할 일 한 건. 루틴 파생(routine_id != NULL) 또는 임시(NULL)."""

    __tablename__ = "todo_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    todo_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # 'pending' | 'done' | 'failed' (models.enums.TodoStatus).
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    routine_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("routines.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    routine: Mapped[Routine | None] = relationship("Routine", foreign_keys=[routine_id])

    __table_args__ = (
        # 같은 루틴이 같은 날 중복 물질화되는 것 방지 — 임시 항목(routine_id NULL)은 제외.
        Index(
            "uq_todo_items_routine_day",
            "user_id",
            "todo_date",
            "routine_id",
            unique=True,
            postgresql_where=text("routine_id IS NOT NULL"),
        ),
        Index("idx_todo_items_user_date", "user_id", "todo_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<TodoItem id={self.id} date={self.todo_date} "
            f"status={self.status} title={self.title!r}>"
        )
