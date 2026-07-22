"""데일리 투두 + 반복 루틴 서비스.

크론 없는 "자정 자동 생성":
  스케줄러를 두지 않고, 사용자가 어떤 날짜의 투두를 조회하는 시점에 그 날짜(<= 오늘)에
  해당하는 활성 루틴을 TodoItem 으로 물질화한다(ensure_day_materialized). 자정이 지나
  새 날짜를 열면 루틴 항목이 자동으로 생겨 보이므로 빈 레코드를 미리 365개 쌓지 않는다.

리포트는 순수 조회다(물질화 부작용 없음) — 실제로 열어본(또는 항목이 생성된) 날의 기록만
집계한다. 달성률 분모는 그날의 전체 항목이다 — 실패(X)는 자기 반성으로 남기는 명시적 미달성이라
분모에서 빼지 않는다(빈칸도 '안 한 것'으로 함께 센다).

"오늘"의 기준 시각대는 Asia/Seoul(KST)로 고정한다 — 국내용 서비스이며 자정 경계가 사용자
체감과 일치해야 하기 때문이다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Routine, TodoItem
from app.models.enums import RoutineFrequency, TodoStatus
from app.schemas.todos import days_to_list, days_to_str

_KST = ZoneInfo("Asia/Seoul")


class TodoServiceError(Exception):
    """투두/루틴 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def today_kst() -> date:
    """서비스 기준 '오늘' (KST). 자정 경계 판정/미래 날짜 가드에 쓴다."""
    return datetime.now(_KST).date()


# --- 루틴 CRUD ---------------------------------------------------------------


async def list_routines(session: AsyncSession, user_id: int) -> list[Routine]:
    """사용자의 루틴 전체(활성/비활성). 정렬순 → 생성순."""
    rows = (
        await session.execute(
            select(Routine)
            .where(Routine.user_id == user_id)
            .order_by(Routine.sort_order.asc(), Routine.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def create_routine(
    session: AsyncSession,
    user_id: int,
    title: str,
    frequency: RoutineFrequency,
    days_of_week: list[int],
    day_of_month: int | None = None,
) -> Routine:
    if frequency == RoutineFrequency.WEEKLY and not days_of_week:
        raise TodoServiceError(422, "주간 루틴은 요일을 1개 이상 선택해야 합니다.")
    if frequency == RoutineFrequency.MONTHLY and day_of_month is None:
        raise TodoServiceError(422, "매월 루틴은 날짜를 선택해야 합니다.")
    routine = Routine(
        user_id=user_id,
        title=title.strip(),
        frequency=frequency.value,
        days_of_week=days_to_str(days_of_week) if frequency == RoutineFrequency.WEEKLY else None,
        day_of_month=day_of_month if frequency == RoutineFrequency.MONTHLY else None,
    )
    session.add(routine)
    await session.commit()
    await session.refresh(routine)
    return routine


async def _get_owned_routine(
    session: AsyncSession, user_id: int, routine_id: int
) -> Routine:
    routine = await session.get(Routine, routine_id)
    if routine is None or routine.user_id != user_id:
        raise TodoServiceError(404, "루틴을 찾을 수 없습니다.")
    return routine


async def update_routine(
    session: AsyncSession,
    user_id: int,
    routine_id: int,
    *,
    title: str | None,
    frequency: RoutineFrequency | None,
    days_of_week: list[int] | None,
    day_of_month: int | None,
    is_active: bool | None,
    sort_order: int | None,
) -> Routine:
    routine = await _get_owned_routine(session, user_id, routine_id)

    if title is not None:
        routine.title = title.strip()
    if frequency is not None:
        routine.frequency = frequency.value
    if days_of_week is not None:
        routine.days_of_week = days_to_str(days_of_week)
    if day_of_month is not None:
        routine.day_of_month = day_of_month
    if is_active is not None:
        routine.is_active = is_active
    if sort_order is not None:
        routine.sort_order = sort_order

    # 최종 상태 정합성 — 주기가 요구하는 값이 없으면 거부하고, 쓰지 않는 값은 지운다.
    if routine.frequency == RoutineFrequency.WEEKLY.value and not days_to_list(
        routine.days_of_week
    ):
        raise TodoServiceError(422, "주간 루틴은 요일을 1개 이상 선택해야 합니다.")
    if routine.frequency == RoutineFrequency.MONTHLY.value and routine.day_of_month is None:
        raise TodoServiceError(422, "매월 루틴은 날짜를 선택해야 합니다.")
    if routine.frequency != RoutineFrequency.WEEKLY.value:
        routine.days_of_week = None
    if routine.frequency != RoutineFrequency.MONTHLY.value:
        routine.day_of_month = None

    await session.commit()
    await session.refresh(routine)
    return routine


async def delete_routine(session: AsyncSession, user_id: int, routine_id: int) -> None:
    """루틴 삭제. 파생 TodoItem 은 FK SET NULL 로 임시 항목이 되어 과거 기록이 보존된다."""
    routine = await _get_owned_routine(session, user_id, routine_id)
    await session.delete(routine)
    await session.commit()


# --- 물질화 (루틴 → 그날의 TodoItem) ----------------------------------------


def routine_applies_on(routine: Routine, day: date) -> bool:
    """루틴이 특정 날짜에 항목을 만들어야 하는지.

    - daily 는 매일. weekly 는 그날 요일이 days_of_week 에 포함될 때.
    - monthly 는 그날 일자가 day_of_month 와 같을 때. 그 달에 없는 날짜(2월 31일 등)는 아예
      맞는 날이 없으므로 그 달만 건너뛴다 — 말일로 당겨 붙이지 않는다.
    - 루틴 생성일 이전 날짜에는 소급 적용하지 않는다(과거 소급 미달성 방지).
    """
    created_day = routine.created_at.astimezone(_KST).date()
    if day < created_day:
        return False
    if routine.frequency == RoutineFrequency.DAILY.value:
        return True
    if routine.frequency == RoutineFrequency.MONTHLY.value:
        return routine.day_of_month is not None and day.day == routine.day_of_month
    return day.weekday() in set(days_to_list(routine.days_of_week))


async def ensure_day_materialized(
    session: AsyncSession, user_id: int, day: date
) -> None:
    """day(<= 오늘)에 해당하는 활성 루틴을 TodoItem 으로 채운다(멱등).

    미래 날짜에는 물질화하지 않는다(루틴 항목을 미리 만들지 않음). 이미 존재하는
    (user, day, routine) 항목은 건너뛴다.
    """
    if day > today_kst():
        return

    active = (
        await session.execute(
            select(Routine).where(
                Routine.user_id == user_id, Routine.is_active.is_(True)
            )
        )
    ).scalars().all()
    if not active:
        return

    existing_routine_ids = set(
        (
            await session.execute(
                select(TodoItem.routine_id).where(
                    TodoItem.user_id == user_id,
                    TodoItem.todo_date == day,
                    TodoItem.routine_id.is_not(None),
                )
            )
        ).scalars().all()
    )

    created = False
    for routine in active:
        if routine.id in existing_routine_ids:
            continue
        if not routine_applies_on(routine, day):
            continue
        session.add(
            TodoItem(
                user_id=user_id,
                todo_date=day,
                title=routine.title,
                status=TodoStatus.PENDING.value,
                routine_id=routine.id,
                sort_order=routine.sort_order,
            )
        )
        created = True

    if created:
        await session.commit()


# --- 투두 항목 조회 / CRUD ---------------------------------------------------


@dataclass
class TodoItemView:
    item: TodoItem
    routine_title: str | None


async def get_day(
    session: AsyncSession, user_id: int, day: date
) -> list[TodoItemView]:
    """하루치 투두를 물질화 후 반환한다(루틴 항목 먼저, 그다음 정렬/생성순)."""
    await ensure_day_materialized(session, user_id, day)

    rows = (
        await session.execute(
            select(TodoItem, Routine.title)
            .outerjoin(Routine, Routine.id == TodoItem.routine_id)
            .where(TodoItem.user_id == user_id, TodoItem.todo_date == day)
            .order_by(
                # 사용자 드래그 정렬을 그대로 존중 — sort_order 우선, 동률은 생성순(id).
                TodoItem.sort_order.asc(),
                TodoItem.id.asc(),
            )
        )
    ).all()
    return [TodoItemView(item=item, routine_title=title) for item, title in rows]


async def create_todo(
    session: AsyncSession, user_id: int, day: date, title: str
) -> TodoItemView:
    """그날의 임시 투두(루틴 아님) 추가. 정렬은 기존 최대 + 1."""
    max_order = (
        await session.execute(
            select(TodoItem.sort_order)
            .where(TodoItem.user_id == user_id, TodoItem.todo_date == day)
            .order_by(TodoItem.sort_order.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    item = TodoItem(
        user_id=user_id,
        todo_date=day,
        title=title.strip(),
        status=TodoStatus.PENDING.value,
        routine_id=None,
        sort_order=(max_order or 0) + 1,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return TodoItemView(item=item, routine_title=None)


async def _get_owned_todo(
    session: AsyncSession, user_id: int, todo_id: int
) -> TodoItem:
    item = await session.get(TodoItem, todo_id)
    if item is None or item.user_id != user_id:
        raise TodoServiceError(404, "할 일을 찾을 수 없습니다.")
    return item


async def update_todo(
    session: AsyncSession,
    user_id: int,
    todo_id: int,
    *,
    title: str | None,
    status: TodoStatus | None,
    sort_order: int | None,
) -> TodoItemView:
    item = await _get_owned_todo(session, user_id, todo_id)

    if title is not None:
        item.title = title.strip()
    if status is not None:
        item.status = status.value
        # 완료 시각 기록 — done 이면 지금(UTC), 아니면 해제.
        item.completed_at = datetime.now(UTC) if status == TodoStatus.DONE else None
    if sort_order is not None:
        item.sort_order = sort_order

    await session.commit()
    await session.refresh(item)

    routine_title: str | None = None
    if item.routine_id is not None:
        routine = await session.get(Routine, item.routine_id)
        routine_title = routine.title if routine else None
    return TodoItemView(item=item, routine_title=routine_title)


async def delete_todo(session: AsyncSession, user_id: int, todo_id: int) -> None:
    """투두 항목 삭제.

    루틴 파생 항목을 지우면 같은 날 재조회 시 다시 물질화된다(설계상 허용 — '실패(X)'로
    기록을 남기려면 삭제 대신 failed 로 두면 된다).
    """
    item = await _get_owned_todo(session, user_id, todo_id)
    await session.delete(item)
    await session.commit()


# --- 리포트 (순수 조회) ------------------------------------------------------


@dataclass
class _DayCount:
    done: int = 0
    failed: int = 0
    pending: int = 0

    @property
    def total(self) -> int:
        return self.done + self.failed + self.pending

    @property
    def actionable(self) -> int:
        """달성률 분모 — 그날의 전체 항목. 실패(X)도 빈칸도 '안 한 것'으로 함께 센다."""
        return self.total

    @property
    def achieved(self) -> bool:
        # 항목이 1개 이상이고 전부 완료여야 그날은 '달성' — 실패가 하나라도 있으면 아니다.
        return self.total >= 1 and self.done == self.total


@dataclass
class RoutineAgg:
    title: str
    done: int = 0
    actionable: int = 0


@dataclass
class ReportData:
    range_start: date
    range_end: date
    total: int
    done: int
    failed: int
    pending: int
    completion_rate: float
    current_streak: int
    longest_streak: int
    daily: list[tuple[date, _DayCount]]
    routines: list[tuple[int | None, RoutineAgg]] = field(default_factory=list)


async def _day_counts(
    session: AsyncSession, user_id: int, start: date, end: date
) -> dict[date, _DayCount]:
    rows = (
        await session.execute(
            select(TodoItem.todo_date, TodoItem.status).where(
                TodoItem.user_id == user_id,
                TodoItem.todo_date >= start,
                TodoItem.todo_date <= end,
            )
        )
    ).all()
    counts: dict[date, _DayCount] = defaultdict(_DayCount)
    for day, status in rows:
        c = counts[day]
        if status == TodoStatus.DONE.value:
            c.done += 1
        elif status == TodoStatus.FAILED.value:
            c.failed += 1
        else:
            c.pending += 1
    return counts


async def _compute_streaks(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """전역 연속 달성(current/longest)을 계산한다.

    - current: 오늘(아직 시작 안 했으면 어제)부터 뒤로 '달성'이 끊길 때까지의 연속 일수.
    - longest: 달성한 날들 중 달력상 최장 연속 구간.
    """
    rows = (
        await session.execute(
            select(TodoItem.todo_date, TodoItem.status).where(
                TodoItem.user_id == user_id,
                TodoItem.todo_date <= today_kst(),
            )
        )
    ).all()
    counts: dict[date, _DayCount] = defaultdict(_DayCount)
    for day, status in rows:
        c = counts[day]
        if status == TodoStatus.DONE.value:
            c.done += 1
        elif status == TodoStatus.FAILED.value:
            c.failed += 1
        else:
            c.pending += 1

    achieved_days = {d for d, c in counts.items() if c.achieved}

    # current streak
    today = today_kst()
    cursor = today
    # 오늘 아직 실행 대상이 없으면(미시작) 어제부터 센다.
    if today not in counts or counts[today].actionable == 0:
        cursor = today - timedelta(days=1)
    current = 0
    while cursor in achieved_days:
        current += 1
        cursor -= timedelta(days=1)

    # longest streak
    longest = 0
    for d in achieved_days:
        if (d - timedelta(days=1)) in achieved_days:
            continue  # 구간 시작점만 확장
        length = 1
        n = d + timedelta(days=1)
        while n in achieved_days:
            length += 1
            n += timedelta(days=1)
        longest = max(longest, length)

    return current, longest


async def _routine_aggregates(
    session: AsyncSession, user_id: int, start: date, end: date
) -> list[tuple[int | None, RoutineAgg]]:
    rows = (
        await session.execute(
            select(TodoItem.routine_id, TodoItem.title, TodoItem.status, Routine.title)
            .outerjoin(Routine, Routine.id == TodoItem.routine_id)
            .where(
                TodoItem.user_id == user_id,
                TodoItem.todo_date >= start,
                TodoItem.todo_date <= end,
                TodoItem.routine_id.is_not(None),
            )
        )
    ).all()
    aggs: dict[int, RoutineAgg] = {}
    for routine_id, item_title, status, routine_title in rows:
        agg = aggs.get(routine_id)
        if agg is None:
            agg = RoutineAgg(title=routine_title or item_title)
            aggs[routine_id] = agg
        agg.actionable += 1  # 완료/실패/빈칸 모두 분모에 들어간다.
        if status == TodoStatus.DONE.value:
            agg.done += 1
    return sorted(aggs.items(), key=lambda kv: kv[1].title)


async def build_report(
    session: AsyncSession, user_id: int, start: date, end: date
) -> ReportData:
    """[start, end] 구간 리포트를 조립한다(순수 조회, 물질화 없음)."""
    if end < start:
        raise TodoServiceError(422, "기간이 올바르지 않습니다.")

    counts = await _day_counts(session, user_id, start, end)

    total = done = failed = pending = 0
    daily: list[tuple[date, _DayCount]] = []
    cursor = start
    while cursor <= end:
        c = counts.get(cursor, _DayCount())
        daily.append((cursor, c))
        total += c.total
        done += c.done
        failed += c.failed
        pending += c.pending
        cursor += timedelta(days=1)

    completion_rate = (done / total) if total > 0 else 0.0

    current_streak, longest_streak = await _compute_streaks(session, user_id)
    routines = await _routine_aggregates(session, user_id, start, end)

    return ReportData(
        range_start=start,
        range_end=end,
        total=total,
        done=done,
        failed=failed,
        pending=pending,
        completion_rate=completion_rate,
        current_streak=current_streak,
        longest_streak=longest_streak,
        daily=daily,
        routines=routines,
    )


def week_bounds(anchor: date) -> tuple[date, date]:
    """anchor 가 속한 주(월요일 시작)의 [월, 일] 범위."""
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """(year, month) 달의 [1일, 말일] 범위."""
    if month < 1 or month > 12:
        raise TodoServiceError(422, "월은 1~12 범위여야 합니다.")
    start = date(year, month, 1)
    if month == 12:
        next_start = date(year + 1, 1, 1)
    else:
        next_start = date(year, month + 1, 1)
    return start, next_start - timedelta(days=1)
