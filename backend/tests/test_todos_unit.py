"""데일리 투두/루틴 서비스 단위 테스트 — DB 없이 가짜 세션.

검증 축:
  - routine_applies_on: daily/weekly 요일 판정 + 생성일 이전 소급 금지
  - week_bounds / month_bounds: 기간 경계(윤년·연말 wrap 포함)
  - _DayCount.achieved: '실행 대상 1개↑ & 미완료 0' 규칙
  - _compute_streaks: 현재/최장 연속(가짜 세션이 (date,status) 행 반환)
  - build_report: 합계·완료율(분모 = 전체 항목, 실패 포함)·일별 포인트 개수
  - 시작 시각 스키마: 10분 단위 강제 + None(= 종일) 허용, 부분 수정의 3갈래 구분
  - 옮기기/복제: origin_date 보존(루틴 재물질화 방지)과 사본의 루틴 링크 절단
멱등 물질화·부분 유니크 인덱스·NULLS FIRST 정렬 등 postgres 특화 동작은 실 DB 통합
시나리오에서 별도 검증한다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.models import Routine, TodoItem
from app.models.enums import TodoStatus
from app.schemas.todos import (
    TodoCreateRequest,
    TodoDuplicateRequest,
    TodoUpdateRequest,
)
from app.services import todos
from app.services.todos import (
    _DayCount,
    build_report,
    duplicate_todo,
    month_bounds,
    routine_applies_on,
    today_kst,
    update_todo,
    week_bounds,
)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """execute 호출마다 준비된 배치를 순서대로 돌려준다(리포트는 3회 실행)."""

    def __init__(self, batches: list[list]) -> None:
        self._batches = batches
        self.calls = 0

    async def execute(self, *_a, **_k) -> _Result:
        rows = self._batches[self.calls] if self.calls < len(self._batches) else []
        self.calls += 1
        return _Result(rows)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeCrudSession:
    """단건 조작(get/add/commit/refresh)용 가짜 세션.

    execute 는 _next_sort_order 의 "그날 최대 정렬값" 조회 하나뿐이라 그 값만 돌려준다.
    """

    def __init__(
        self,
        items: dict[int, TodoItem],
        routines: dict[int, Routine] | None = None,
        max_sort_order: int | None = None,
    ) -> None:
        self._items = items
        self._routines = routines or {}
        self._max_sort_order = max_sort_order
        self.added: list[TodoItem] = []

    async def get(self, model: type, pk: int) -> object | None:
        return self._routines.get(pk) if model is Routine else self._items.get(pk)

    def add(self, obj: TodoItem) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: object) -> None:
        return None

    async def execute(self, *_a: object, **_k: object) -> _ScalarResult:
        return _ScalarResult(self._max_sort_order)


def _todo(
    todo_id: int,
    day: dt.date,
    *,
    origin: dt.date | None = None,
    routine_id: int | None = None,
    status: str = "pending",
    start_time: dt.time | None = None,
    sort_order: int = 0,
) -> TodoItem:
    item = TodoItem()
    item.id = todo_id
    item.user_id = 1
    item.todo_date = day
    item.origin_date = origin or day
    item.title = "운동"
    item.status = status
    item.routine_id = routine_id
    item.start_time = start_time
    item.sort_order = sort_order
    item.completed_at = None
    return item


def _routine(
    freq: str,
    days: str | None = None,
    created: dt.datetime | None = None,
    day_of_month: int | None = None,
) -> Routine:
    r = Routine()
    r.frequency = freq
    r.days_of_week = days
    r.day_of_month = day_of_month
    r.created_at = created or dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    return r


class TestRoutineApplies:
    def test_daily_applies_every_day_after_creation(self) -> None:
        r = _routine("daily", created=dt.datetime(2026, 7, 1, tzinfo=dt.UTC))
        assert routine_applies_on(r, dt.date(2026, 7, 21)) is True
        # 생성일 당일은 적용, 이전 날짜는 소급하지 않음.
        assert routine_applies_on(r, dt.date(2026, 7, 1)) is True
        assert routine_applies_on(r, dt.date(2026, 6, 30)) is False

    def test_weekly_matches_only_selected_weekdays(self) -> None:
        # 월(0)·수(2)·금(4)
        r = _routine("weekly", days="0,2,4", created=dt.datetime(2026, 7, 1, tzinfo=dt.UTC))
        base = dt.date(2026, 7, 6)  # 2026-07-06 = 월요일
        for offset in range(7):
            day = base + dt.timedelta(days=offset)
            expected = day.weekday() in {0, 2, 4}
            assert routine_applies_on(r, day) is expected

    def test_monthly_matches_only_that_day_each_month(self) -> None:
        r = _routine("monthly", day_of_month=15, created=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
        assert routine_applies_on(r, dt.date(2026, 7, 15)) is True
        assert routine_applies_on(r, dt.date(2026, 8, 15)) is True
        assert routine_applies_on(r, dt.date(2026, 7, 14)) is False
        # 생성일 이전은 다른 주기와 마찬가지로 소급하지 않는다.
        assert routine_applies_on(r, dt.date(2025, 12, 15)) is False

    def test_monthly_skips_months_without_that_day(self) -> None:
        """31일 루틴은 30일까지인 달을 통째로 건너뛴다 — 말일로 당겨 붙이지 않는다."""
        r = _routine("monthly", day_of_month=31, created=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
        assert routine_applies_on(r, dt.date(2026, 1, 31)) is True
        # 2026-02 는 28일까지, 2026-04 는 30일까지 — 맞는 날짜 자체가 없다.
        for day in (dt.date(2026, 2, 28), dt.date(2026, 4, 30)):
            assert routine_applies_on(r, day) is False


class TestStartTime:
    def test_accepts_ten_minute_marks_and_all_day(self) -> None:
        day = dt.date(2026, 8, 3)

        def start_time(v: dt.time | None) -> dt.time | None:
            return TodoCreateRequest(date=day, title="회의", start_time=v).start_time

        assert start_time(dt.time(9, 30)) == dt.time(9, 30)
        assert start_time(dt.time(0, 0)) == dt.time(0, 0)
        # 생략하면 종일 — 정렬에서 시각 있는 항목보다 위로 간다.
        assert TodoCreateRequest(date=day, title="회의").start_time is None

    def test_rejects_off_grid_minutes_and_seconds(self) -> None:
        day = dt.date(2026, 8, 3)
        for bad in (dt.time(9, 35), dt.time(9, 31), dt.time(9, 30, 30)):
            with pytest.raises(ValidationError):
                TodoCreateRequest(date=day, title="회의", start_time=bad)

    def test_update_distinguishes_unset_from_explicit_null(self) -> None:
        """start_time 은 null 자체가 '종일로 지움'이라 미변경과 값으로는 갈리지 않는다."""
        # 안 보냄 → 라우터가 set_start_time=False 로 읽어 건드리지 않는다.
        untouched = TodoUpdateRequest.model_validate({"status": "done"})
        assert "start_time" not in untouched.model_fields_set
        # 명시적 null → 종일로 지운다.
        cleared = TodoUpdateRequest.model_validate({"start_time": None})
        assert "start_time" in cleared.model_fields_set
        assert cleared.start_time is None


class TestBounds:
    def test_week_bounds_is_monday_to_sunday(self) -> None:
        start, end = week_bounds(dt.date(2026, 7, 21))  # 화요일
        assert start.weekday() == 0
        assert end == start + dt.timedelta(days=6)
        assert start <= dt.date(2026, 7, 21) <= end

    def test_month_bounds_regular_and_leap_and_wrap(self) -> None:
        assert month_bounds(2026, 2) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
        assert month_bounds(2024, 2) == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))
        assert month_bounds(2026, 12) == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))


class TestDayCount:
    def test_achieved_requires_all_done(self) -> None:
        """실패(X)는 명시적 미달성 — 하나라도 있으면 그날은 '달성'이 아니다."""
        assert _DayCount(done=2, failed=0, pending=0).achieved is True
        assert _DayCount(done=2, failed=1, pending=0).achieved is False  # 실패 존재
        assert _DayCount(done=0, failed=3, pending=0).achieved is False  # 전부 실패
        assert _DayCount(done=1, failed=0, pending=1).achieved is False  # 빈칸 존재
        assert _DayCount().achieved is False  # 항목 없음
        # 분모는 전체 항목(완료+실패+빈칸).
        assert _DayCount(done=1, failed=1, pending=1).actionable == 3


class TestStreaks:
    async def test_current_and_longest_streak(self) -> None:
        today = today_kst()
        rows = [
            (today, "done"),
            (today - dt.timedelta(days=1), "done"),
            (today - dt.timedelta(days=2), "done"),
            (today - dt.timedelta(days=3), "pending"),  # 여기서 끊김
            (today - dt.timedelta(days=5), "done"),  # 고립된 하루
        ]
        current, longest = await todos._compute_streaks(_FakeSession([rows]), user_id=1)  # type: ignore[arg-type]
        assert current == 3
        assert longest == 3

    async def test_today_not_started_counts_from_yesterday(self) -> None:
        today = today_kst()
        rows = [
            (today - dt.timedelta(days=1), "done"),
            (today - dt.timedelta(days=2), "done"),
        ]
        current, _ = await todos._compute_streaks(_FakeSession([rows]), user_id=1)  # type: ignore[arg-type]
        assert current == 2  # 오늘은 항목이 없어 어제부터 카운트


class TestBuildReport:
    async def test_totals_completion_and_daily_points(self) -> None:
        start = dt.date(2026, 7, 6)
        end = dt.date(2026, 7, 8)  # 3일
        day_rows = [
            (dt.date(2026, 7, 6), "done"),
            (dt.date(2026, 7, 6), "pending"),
            (dt.date(2026, 7, 7), "done"),
            (dt.date(2026, 7, 7), "failed"),
            (dt.date(2026, 7, 8), "done"),
        ]
        streak_rows: list = []
        routine_rows = [
            (10, "운동", "done", "운동"),
            (10, "운동", "pending", "운동"),
        ]
        report = await build_report(
            _FakeSession([day_rows, streak_rows, routine_rows]),  # type: ignore[arg-type]
            user_id=1,
            start=start,
            end=end,
        )
        assert report.done == 3
        assert report.pending == 1
        assert report.failed == 1
        assert report.total == 5
        # 완료율 = done / total = 3/5 — 실패(X)도 빈칸도 분모에 들어간다.
        assert abs(report.completion_rate - 0.6) < 1e-9
        assert len(report.daily) == 3  # 빈 날 없이 3일 모두 포인트
        # 루틴 집계: 운동 done 1 / actionable 2.
        assert len(report.routines) == 1
        rid, agg = report.routines[0]
        assert rid == 10
        assert agg.done == 1
        assert agg.actionable == 2


class TestMoveAndDuplicate:
    """다른 날로 옮기기 / 사본 만들기.

    옮기기의 핵심 계약은 "todo_date 만 움직이고 origin_date 는 남는다"는 것이다. 루틴 물질화가
    origin_date 로 그날치 존재 여부를 묻기 때문에, 이 값이 따라 움직이면 미룬 일이 원래 날짜에
    도로 생기고 옮겨 간 날에서는 그날치와 유니크 인덱스가 부딪힌다.
    """

    async def test_move_keeps_origin_date_and_appends_to_target_day(self) -> None:
        item = _todo(1, dt.date(2026, 8, 10), routine_id=7, sort_order=3)
        routine = _routine("daily")
        routine.title = "운동"
        session = _FakeCrudSession({1: item}, {7: routine}, max_sort_order=5)

        view = await update_todo(
            session,  # type: ignore[arg-type]
            user_id=1,
            todo_id=1,
            title=None,
            status=None,
            start_time=None,
            sort_order=None,
            todo_date=dt.date(2026, 8, 11),
        )

        assert view.item.todo_date == dt.date(2026, 8, 11)
        # 원래 날짜 자리는 그대로 점유한다 — 8/10 을 다시 열어도 이 루틴은 재생성되지 않는다.
        assert view.item.origin_date == dt.date(2026, 8, 10)
        # 옮겨 간 날의 맨 뒤로 붙는다(그날 최대 5 + 1).
        assert view.item.sort_order == 6
        # 루틴 링크는 유지된다 — 옮겼을 뿐 여전히 그 루틴의 8/10 몫이다.
        assert view.item.routine_id == 7
        assert view.routine_title == "운동"

    async def test_move_respects_explicit_sort_order(self) -> None:
        """정렬값을 함께 지정하면 대상 날짜 맨 뒤로 밀지 않고 그 값을 쓴다."""
        item = _todo(1, dt.date(2026, 8, 10), sort_order=3)
        session = _FakeCrudSession({1: item}, max_sort_order=5)

        view = await update_todo(
            session,  # type: ignore[arg-type]
            user_id=1,
            todo_id=1,
            title=None,
            status=None,
            start_time=None,
            sort_order=0,
            todo_date=dt.date(2026, 8, 11),
        )

        assert view.item.sort_order == 0

    async def test_same_date_is_not_a_move(self) -> None:
        item = _todo(1, dt.date(2026, 8, 10), sort_order=3)
        session = _FakeCrudSession({1: item}, max_sort_order=5)

        view = await update_todo(
            session,  # type: ignore[arg-type]
            user_id=1,
            todo_id=1,
            title=None,
            status=None,
            start_time=None,
            sort_order=None,
            todo_date=dt.date(2026, 8, 10),
        )

        # 같은 날로 '옮기면' 정렬값까지 그대로여야 한다(맨 뒤로 밀려나지 않는다).
        assert view.item.sort_order == 3

    async def test_duplicate_detaches_routine_and_resets_progress(self) -> None:
        source = _todo(
            1,
            dt.date(2026, 8, 10),
            routine_id=7,
            status=TodoStatus.DONE.value,
            start_time=dt.time(9, 30),
        )
        source.completed_at = dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC)
        session = _FakeCrudSession({1: source}, max_sort_order=4)

        view = await duplicate_todo(
            session,  # type: ignore[arg-type]
            user_id=1,
            todo_id=1,
            target_date=dt.date(2026, 8, 12),
        )

        copy = view.item
        assert session.added == [copy]
        # 사본은 루틴이 만든 그날치가 아니라 손으로 늘린 별개의 일이다.
        assert copy.routine_id is None
        assert view.routine_title is None
        # 아직 하지 않은 일이므로 진행 상태는 처음으로 돌린다.
        assert copy.status == TodoStatus.PENDING.value
        assert copy.completed_at is None
        # 옮겨 온 것이 아니라 그날 태어난 항목이다 — 두 날짜가 같아야 '옮김' 배지가 안 뜬다.
        assert copy.todo_date == copy.origin_date == dt.date(2026, 8, 12)
        assert copy.title == source.title
        assert copy.start_time == dt.time(9, 30)
        assert copy.sort_order == 5
        # 원본은 손대지 않는다.
        assert source.todo_date == dt.date(2026, 8, 10)
        assert source.status == TodoStatus.DONE.value

    async def test_duplicate_without_target_lands_on_source_day(self) -> None:
        source = _todo(1, dt.date(2026, 8, 10))
        session = _FakeCrudSession({1: source}, max_sort_order=None)

        view = await duplicate_todo(
            session,  # type: ignore[arg-type]
            user_id=1,
            todo_id=1,
            target_date=None,
        )

        assert view.item.todo_date == dt.date(2026, 8, 10)
        # 그날 항목이 하나도 없을 때(최대값 없음)의 정렬값은 1 이다.
        assert view.item.sort_order == 1

    def test_request_schemas_carry_target_date(self) -> None:
        # date 는 start_time 과 달리 null 이 '값'이 아니라 미변경이라 3갈래를 가르지 않는다.
        assert TodoUpdateRequest.model_validate({"status": "done"}).date is None
        moved = TodoUpdateRequest.model_validate({"date": "2026-08-11"})
        assert moved.date == dt.date(2026, 8, 11)
        # 복제는 날짜를 생략하면 서비스가 원본 날짜로 채운다.
        assert TodoDuplicateRequest().date is None
        assert TodoDuplicateRequest.model_validate({"date": None}).date is None
