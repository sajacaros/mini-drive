/** 데일리 투두 + 반복 루틴 API. */

import apiClient from "./client";
import type {
  Routine,
  RoutineCreateRequest,
  RoutineListResponse,
  RoutineUpdateRequest,
  TodoDayResponse,
  TodoItem,
  TodoReport,
  TodoStatus,
} from "./types";

// --- 투두 ------------------------------------------------------------------

/** 하루치 투두 (date 미지정 시 서버 기준 오늘). 조회 시 루틴이 자동 물질화된다. */
export async function getDay(date?: string): Promise<TodoDayResponse> {
  const { data } = await apiClient.get<TodoDayResponse>("/todos", {
    params: date ? { date } : undefined,
  });
  return data;
}

export async function createTodo(date: string, title: string): Promise<TodoItem> {
  const { data } = await apiClient.post<TodoItem>("/todos", { date, title });
  return data;
}

export async function updateTodo(
  id: number,
  payload: { title?: string; status?: TodoStatus; sort_order?: number },
): Promise<TodoItem> {
  const { data } = await apiClient.patch<TodoItem>(`/todos/${id}`, payload);
  return data;
}

export async function deleteTodo(id: number): Promise<void> {
  await apiClient.delete(`/todos/${id}`);
}

// --- 루틴 ------------------------------------------------------------------

export async function listRoutines(): Promise<RoutineListResponse> {
  const { data } = await apiClient.get<RoutineListResponse>("/todos/routines");
  return data;
}

export async function createRoutine(payload: RoutineCreateRequest): Promise<Routine> {
  const { data } = await apiClient.post<Routine>("/todos/routines", payload);
  return data;
}

export async function updateRoutine(
  id: number,
  payload: RoutineUpdateRequest,
): Promise<Routine> {
  const { data } = await apiClient.put<Routine>(`/todos/routines/${id}`, payload);
  return data;
}

export async function deleteRoutine(id: number): Promise<void> {
  await apiClient.delete(`/todos/routines/${id}`);
}

// --- 리포트 ----------------------------------------------------------------

/** date 가 속한 주(월~일) 리포트. 미지정 시 이번 주. */
export async function weeklyReport(date?: string): Promise<TodoReport> {
  const { data } = await apiClient.get<TodoReport>("/todos/reports/weekly", {
    params: date ? { date } : undefined,
  });
  return data;
}

/** (year, month) 달 리포트. 미지정 시 이번 달. */
export async function monthlyReport(year?: number, month?: number): Promise<TodoReport> {
  const { data } = await apiClient.get<TodoReport>("/todos/reports/monthly", {
    params: year && month ? { year, month } : undefined,
  });
  return data;
}
