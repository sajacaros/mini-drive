/**
 * 사이드 네비 "오늘 할 일" 배지 store — 오늘의 (완료/전체) 카운트.
 *
 * 진실 소스는 서버(GET /api/todos)지만, TodoPage 가 오늘 데이터를 들고 있을 때는
 * reportDay 로 밀어 넣어 별도 재조회 없이 체크/추가/삭제와 함께 배지가 갱신된다.
 * Layout 은 페이지 이동 시 refresh 로 따라잡는다(/todo 제외 — 페이지가 직접 밀어 준다).
 */

import { create } from "zustand";

import { getDay } from "@/api/todos";
import type { TodoDayResponse } from "@/api/types";
import { todayStr } from "@/lib/localDate";

interface TodoBadgeState {
  /** 카운트가 가리키는 날짜. 오늘이 아니면(자정이 지났으면) 배지를 그리지 않는다. */
  date: string | null;
  done: number;
  total: number;
  /** 서버에서 오늘 카운트를 다시 읽는다. 실패는 조용히 무시한다 — 배지는 장식이다. */
  refresh: () => Promise<void>;
  /** 페이지가 이미 들고 있는 하루치 응답을 반영한다(오늘 날짜가 아니면 무시). */
  reportDay: (day: TodoDayResponse) => void;
}

let inFlight = false;

export const useTodoBadgeStore = create<TodoBadgeState>((set) => ({
  date: null,
  done: 0,
  total: 0,

  refresh: async () => {
    if (inFlight) return;
    inFlight = true;
    try {
      const day = await getDay(todayStr());
      set({ date: day.date, done: day.done, total: day.total });
    } catch {
      // 네트워크/일시 오류면 이전 값(또는 없음)으로 둔다.
    } finally {
      inFlight = false;
    }
  },

  reportDay: (day) => {
    if (day.date !== todayStr()) return;
    set({ date: day.date, done: day.done, total: day.total });
  },
}));
