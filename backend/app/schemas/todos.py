"""데일리 투두 + 반복 루틴 API 요청·응답 스키마."""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, Field, field_validator

from app.models.enums import RoutineFrequency, TodoStatus


def validate_start_time(value: time | None) -> time | None:
    """시작 시각은 10분 단위 정각만 받는다(None = 종일).

    할 일 목록은 분 단위로 촘촘히 쪼갤 대상이 아니라 "9시 반쯤" 정도의 눈금이면 충분하다.
    입력 UI 도 10분 단위 선택이라 초/자투리 분은 들어올 일이 없지만, 클라이언트를 믿지 않고
    서버에서 한 번 더 막는다.
    """
    if value is None:
        return None
    if value.minute % 10 != 0 or value.second != 0 or value.microsecond != 0:
        raise ValueError("시작 시각은 10분 단위여야 합니다.")
    return value


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
    """반복 루틴 생성. weekly 면 days_of_week(월=0..일=6), monthly 면 day_of_month(1~31) 필수."""

    title: str = Field(min_length=1, max_length=500)
    frequency: RoutineFrequency = RoutineFrequency.DAILY
    days_of_week: list[int] = Field(default_factory=list)
    day_of_month: int | None = Field(default=None, ge=1, le=31)

    @field_validator("days_of_week")
    @classmethod
    def _valid_days(cls, v: list[int]) -> list[int]:
        for d in v:
            if d < 0 or d > 6:
                raise ValueError("요일은 0(월)~6(일) 범위여야 합니다.")
        return v


class RoutineUpdateRequest(BaseModel):
    """루틴 부분 수정 (제목/주기/요일/날짜/활성 여부/정렬)."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    frequency: RoutineFrequency | None = None
    days_of_week: list[int] | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
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
    day_of_month: int | None = None
    # 파생 항목에 복사되는 시작 시각. 지금은 항상 None(루틴 = 종일 → 목록 맨 위).
    start_time: time | None = None
    is_active: bool
    sort_order: int
    created_at: datetime


class RoutineListResponse(BaseModel):
    items: list[RoutineResponse]


# --- 투두 항목 --------------------------------------------------------------


class TodoCreateRequest(BaseModel):
    """특정 날짜에 임시 투두 추가. start_time 을 생략하거나 null 로 두면 종일이다."""

    date: date
    title: str = Field(min_length=1, max_length=500)
    start_time: time | None = None

    @field_validator("start_time")
    @classmethod
    def _valid_start_time(cls, v: time | None) -> time | None:
        return validate_start_time(v)


class TodoUpdateRequest(BaseModel):
    """투두 부분 수정 (제목/상태/시작 시각/정렬). 체크 순환: pending → done → failed → pending.

    start_time 은 "안 보냄 = 그대로", "null = 종일로 지움", "값 = 그 시각" 세 갈래다. 앞의 둘이
    같은 None 으로 도착하므로 라우터가 model_fields_set 으로 구분한다.
    """

    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: TodoStatus | None = None
    start_time: time | None = None
    sort_order: int | None = None

    @field_validator("start_time")
    @classmethod
    def _valid_start_time(cls, v: time | None) -> time | None:
        return validate_start_time(v)


class TodoResponse(BaseModel):
    id: int
    date: date
    title: str
    status: TodoStatus
    routine_id: int | None = None
    routine_title: str | None = None
    # None = 종일 — 정렬에서 시각 있는 항목보다 앞선다.
    start_time: time | None = None
    sort_order: int
    completed_at: datetime | None = None
    created_at: datetime


class TodoDayResponse(BaseModel):
    """하루치 투두 묶음 + 요약 카운트."""

    date: date
    items: list[TodoResponse]
    total: int  # 전체 항목 수
    done: int
    failed: int  # 수행 실패(X) — 달성률 분모에 포함된다.
    pending: int


# --- 리포트 -----------------------------------------------------------------


class DailyPoint(BaseModel):
    """일별 추이 한 점. 분모는 total(완료+실패+빈칸) 이다."""

    date: date
    total: int
    done: int
    failed: int
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
    failed: int
    pending: int
    completion_rate: float  # done / total (0.0~1.0), total 0 이면 0.0
    current_streak: int
    longest_streak: int
    daily: list[DailyPoint]
    routines: list[RoutineStat]
