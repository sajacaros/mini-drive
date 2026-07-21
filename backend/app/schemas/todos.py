"""데일리 투두 + 반복 루틴 API 요청·응답 스키마."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import RoutineFrequency, TodoStatus


def days_to_list(value: str | None) -> list[int]:
    """"0,1,2" 형태 문자열을 요일 정수 리스트로 파싱한다(빈 값이면 [])."""
    if not value:
        return []
    return [int(x) for x in value.split(",") if x.strip() != ""]


def days_to_str(days: list[int] | None) -> str | None:
    """요일 정수 리스트를 정렬·중복 제거해 "0,1,2" 문자열로. 비면 None."""
    if not days:
        return None
    return ",".join(str(d) for d in sorted(set(days)))


# --- 루틴 -------------------------------------------------------------------


class RoutineCreateRequest(BaseModel):
    """반복 루틴 생성. weekly 면 days_of_week(월=0..일=6) 필수."""

    title: str = Field(min_length=1, max_length=500)
    frequency: RoutineFrequency = RoutineFrequency.DAILY
    days_of_week: list[int] = Field(default_factory=list)

    @field_validator("days_of_week")
    @classmethod
    def _valid_days(cls, v: list[int]) -> list[int]:
        for d in v:
            if d < 0 or d > 6:
                raise ValueError("요일은 0(월)~6(일) 범위여야 합니다.")
        return v


class RoutineUpdateRequest(BaseModel):
    """루틴 부분 수정 (제목/주기/요일/활성 여부/정렬)."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    frequency: RoutineFrequency | None = None
    days_of_week: list[int] | None = None
    is_active: bool | None = None
    sort_order: int | None = None

    @field_validator("days_of_week")
    @classmethod
    def _valid_days(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        for d in v:
            if d < 0 or d > 6:
                raise ValueError("요일은 0(월)~6(일) 범위여야 합니다.")
        return v


class RoutineResponse(BaseModel):
    id: int
    title: str
    frequency: RoutineFrequency
    days_of_week: list[int]
    is_active: bool
    sort_order: int
    created_at: datetime


class RoutineListResponse(BaseModel):
    items: list[RoutineResponse]


# --- 투두 항목 --------------------------------------------------------------


class TodoCreateRequest(BaseModel):
    """특정 날짜에 임시 투두 추가."""

    date: date
    title: str = Field(min_length=1, max_length=500)


class TodoUpdateRequest(BaseModel):
    """투두 부분 수정 (제목/상태/정렬). 체크=done, X=skipped."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: TodoStatus | None = None
    sort_order: int | None = None


class TodoResponse(BaseModel):
    id: int
    date: date
    title: str
    status: TodoStatus
    routine_id: int | None = None
    routine_title: str | None = None
    sort_order: int
    completed_at: datetime | None = None
    created_at: datetime


class TodoDayResponse(BaseModel):
    """하루치 투두 묶음 + 요약 카운트."""

    date: date
    items: list[TodoResponse]
    total: int  # 전체 항목 수
    done: int
    skipped: int
    pending: int


# --- 리포트 -----------------------------------------------------------------


class DailyPoint(BaseModel):
    """일별 추이 한 점. actionable = done + pending (skipped 제외)."""

    date: date
    total: int
    done: int
    skipped: int
    pending: int


class RoutineStat(BaseModel):
    """기간 내 루틴별 달성률."""

    routine_id: int | None
    title: str
    done: int
    actionable: int
    rate: float  # 0.0 ~ 1.0


class ReportResponse(BaseModel):
    """주별/월별 리포트."""

    range_start: date
    range_end: date
    total: int
    done: int
    skipped: int
    pending: int
    completion_rate: float  # done / actionable (0.0~1.0), actionable 0 이면 0.0
    current_streak: int
    longest_streak: int
    daily: list[DailyPoint]
    routines: list[RoutineStat]
