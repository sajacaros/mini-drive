"""데일리 투두 + 반복 루틴 라우터 (/api/todos).

- 크론 없는 자정 자동 생성: GET /api/todos?date= 조회 시 그날의 활성 루틴이 물질화된다.
- 리터럴 경로(/routines, /reports/*)를 파라미터 경로(/{todo_id}) 보다 먼저 선언한다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DbSession
from app.models import Routine
from app.models.enums import RoutineFrequency, TodoStatus
from app.schemas.todos import (
    DailyPoint,
    ReportResponse,
    RoutineCreateRequest,
    RoutineListResponse,
    RoutineResponse,
    RoutineStat,
    RoutineUpdateRequest,
    TodoCreateRequest,
    TodoDayResponse,
    TodoResponse,
    TodoUpdateRequest,
    days_to_list,
)
from app.services import todos as todos_service
from app.services.todos import TodoItemView, TodoServiceError

router = APIRouter()


def _http_error(exc: TodoServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _routine_response(routine: Routine) -> RoutineResponse:
    return RoutineResponse(
        id=routine.id,
        title=routine.title,
        frequency=RoutineFrequency(routine.frequency),
        days_of_week=days_to_list(routine.days_of_week),
        day_of_month=routine.day_of_month,
        start_time=routine.start_time,
        is_active=routine.is_active,
        sort_order=routine.sort_order,
        created_at=routine.created_at,
    )


def _todo_response(view: TodoItemView) -> TodoResponse:
    item = view.item
    return TodoResponse(
        id=item.id,
        date=item.todo_date,
        title=item.title,
        status=TodoStatus(item.status),
        routine_id=item.routine_id,
        routine_title=view.routine_title,
        start_time=item.start_time,
        sort_order=item.sort_order,
        completed_at=item.completed_at,
        created_at=item.created_at,
    )


# --- 루틴 -------------------------------------------------------------------


@router.get("/routines", response_model=RoutineListResponse)
async def list_routines(user: CurrentUser, session: DbSession) -> RoutineListResponse:
    routines = await todos_service.list_routines(session, user.id)
    return RoutineListResponse(items=[_routine_response(r) for r in routines])


@router.post("/routines", response_model=RoutineResponse, status_code=201)
async def create_routine(
    payload: RoutineCreateRequest, user: CurrentUser, session: DbSession
) -> RoutineResponse:
    try:
        routine = await todos_service.create_routine(
            session,
            user.id,
            title=payload.title,
            frequency=payload.frequency,
            days_of_week=payload.days_of_week,
            day_of_month=payload.day_of_month,
        )
    except TodoServiceError as exc:
        raise _http_error(exc) from exc
    return _routine_response(routine)


@router.put("/routines/{routine_id}", response_model=RoutineResponse)
async def update_routine(
    routine_id: int,
    payload: RoutineUpdateRequest,
    user: CurrentUser,
    session: DbSession,
) -> RoutineResponse:
    try:
        routine = await todos_service.update_routine(
            session,
            user.id,
            routine_id,
            title=payload.title,
            frequency=payload.frequency,
            days_of_week=payload.days_of_week,
            day_of_month=payload.day_of_month,
            is_active=payload.is_active,
            sort_order=payload.sort_order,
        )
    except TodoServiceError as exc:
        raise _http_error(exc) from exc
    return _routine_response(routine)


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(
    routine_id: int, user: CurrentUser, session: DbSession
) -> None:
    try:
        await todos_service.delete_routine(session, user.id, routine_id)
    except TodoServiceError as exc:
        raise _http_error(exc) from exc


# --- 리포트 -----------------------------------------------------------------


def _report_response(data: todos_service.ReportData) -> ReportResponse:
    return ReportResponse(
        range_start=data.range_start,
        range_end=data.range_end,
        total=data.total,
        done=data.done,
        failed=data.failed,
        pending=data.pending,
        completion_rate=round(data.completion_rate, 4),
        current_streak=data.current_streak,
        longest_streak=data.longest_streak,
        daily=[
            DailyPoint(
                date=d,
                total=c.total,
                done=c.done,
                failed=c.failed,
                pending=c.pending,
            )
            for d, c in data.daily
        ],
        routines=[
            RoutineStat(
                routine_id=rid,
                title=agg.title,
                done=agg.done,
                actionable=agg.actionable,
                rate=round(agg.done / agg.actionable, 4) if agg.actionable > 0 else 0.0,
            )
            for rid, agg in data.routines
        ],
    )


@router.get("/reports/weekly", response_model=ReportResponse)
async def weekly_report(
    user: CurrentUser,
    session: DbSession,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> ReportResponse:
    """day 가 속한 주(월~일) 리포트. 미지정 시 이번 주."""
    anchor = day or todos_service.today_kst()
    start, end = todos_service.week_bounds(anchor)
    try:
        data = await todos_service.build_report(session, user.id, start, end)
    except TodoServiceError as exc:
        raise _http_error(exc) from exc
    return _report_response(data)


@router.get("/reports/monthly", response_model=ReportResponse)
async def monthly_report(
    user: CurrentUser,
    session: DbSession,
    year: Annotated[int | None, Query()] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
) -> ReportResponse:
    """(year, month) 달 리포트. 미지정 시 이번 달."""
    today = todos_service.today_kst()
    try:
        start, end = todos_service.month_bounds(year or today.year, month or today.month)
        data = await todos_service.build_report(session, user.id, start, end)
    except TodoServiceError as exc:
        raise _http_error(exc) from exc
    return _report_response(data)


# --- 투두 항목 --------------------------------------------------------------


@router.get("", response_model=TodoDayResponse)
async def get_day(
    user: CurrentUser,
    session: DbSession,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> TodoDayResponse:
    """하루치 투두 (미지정 시 오늘). 조회 시점에 그날 루틴이 물질화된다."""
    target = day or todos_service.today_kst()
    views = await todos_service.get_day(session, user.id, target)
    items = [_todo_response(v) for v in views]
    done = sum(1 for v in views if v.item.status == TodoStatus.DONE.value)
    failed = sum(1 for v in views if v.item.status == TodoStatus.FAILED.value)
    pending = sum(1 for v in views if v.item.status == TodoStatus.PENDING.value)
    return TodoDayResponse(
        date=target,
        items=items,
        total=len(items),
        done=done,
        failed=failed,
        pending=pending,
    )


@router.post("", response_model=TodoResponse, status_code=201)
async def create_todo(
    payload: TodoCreateRequest, user: CurrentUser, session: DbSession
) -> TodoResponse:
    view = await todos_service.create_todo(
        session, user.id, payload.date, payload.title, payload.start_time
    )
    return _todo_response(view)


@router.patch("/{todo_id}", response_model=TodoResponse)
async def update_todo(
    todo_id: int,
    payload: TodoUpdateRequest,
    user: CurrentUser,
    session: DbSession,
) -> TodoResponse:
    try:
        view = await todos_service.update_todo(
            session,
            user.id,
            todo_id,
            title=payload.title,
            status=payload.status,
            start_time=payload.start_time,
            sort_order=payload.sort_order,
            # start_time 은 null 자체가 '종일로 지움'이라 값만으로는 미변경과 구분되지
            # 않는다 — 요청 본문에 필드가 실제로 실려 왔는지로 가른다.
            set_start_time="start_time" in payload.model_fields_set,
        )
    except TodoServiceError as exc:
        raise _http_error(exc) from exc
    return _todo_response(view)


@router.delete("/{todo_id}", status_code=204)
async def delete_todo(todo_id: int, user: CurrentUser, session: DbSession) -> None:
    try:
        await todos_service.delete_todo(session, user.id, todo_id)
    except TodoServiceError as exc:
        raise _http_error(exc) from exc
