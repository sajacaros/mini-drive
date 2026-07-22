"""데일리 투두/루틴 서비스 단위 테스트 — DB 없이 가짜 세션.

검증 축:
  - routine_applies_on: daily/weekly 요일 판정 + 생성일 이전 소급 금지
  - week_bounds / month_bounds: 기간 경계(윤년·연말 wrap 포함)
  - _DayCount.achieved: '실행 대상 1개↑ & 미완료 0' 규칙
  - _compute_streaks: 현재/최장 연속(가짜 세션이 (date,status) 행 반환)
  - build_report: 합계·완료율(분모 = 전체 항목, 실패 포함)·일별 포인트 개수
멱등 물질화·부분 유니크 인덱스 등 postgres 특화 동작은 실 DB 통합 시나리오에서 별도 검증한다.
"""

from __future__ import annotations

import datetime as dt

from app.models import Routine
from app.services import todos
from app.services.todos import (
    _DayCount,
    build_report,
    month_bounds,
    routine_applies_on,
    today_kst,
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


def _routine(freq: str, days: str | None = None, created: dt.datetime | None = None) -> Routine:
    r = Routine()
    r.frequency = freq
    r.days_of_week = days
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
