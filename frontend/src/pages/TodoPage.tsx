/**
 * 데일리 투두 — 날짜별 할 일. 날짜를 이동하며 조회하고(자정이 지나 새 날을 열면 활성 루틴이
 * 서버에서 자동 물질화된다), 각 항목의 체크를 누를 때마다 상태가 빈칸 → 완료(v) → 실패(x) →
 * 빈칸 으로 돈다. X 는 '오늘은 안 함(skip)'이 아니라 자기 반성으로 남기는 명시적 수행 실패이며,
 * 달성률 분모에서 빠지지 않는다. 임시 항목은 그날 직접 추가/삭제할 수 있고, 루틴 파생 항목은
 * 배지로 구분한다(삭제 불가 — 실패로 기록).
 *
 * 순서: 종일(start_time = null)이 맨 위, 그 아래로 시작 시각순이다. 루틴은 언제나 종일이라
 * 항상 위에 모인다. 시각이 있는 항목은 시각이 순서를 결정하므로 드래그 핸들을 내리고, 종일
 * 항목끼리만 드래그로 순서를 잡는다 — 그래야 놓은 자리에서 도로 튕기는 일이 없다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import { createTodo, deleteTodo, getDay, updateTodo } from "@/api/todos";
import type { TodoDayResponse, TodoItem, TodoStatus } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import {
  CalendarIcon,
  ChartIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClockIcon,
  GripIcon,
  PlusIcon,
  RepeatIcon,
  TrashIcon,
  XIcon,
} from "@/components/icons";
import {
  HOUR_OPTIONS,
  MINUTE_OPTIONS,
  addDays,
  formatDayLabel,
  formatTimeLabel,
  parseTimeStr,
  toTimeStr,
  todayStr,
} from "@/lib/localDate";
import { useTodoBadgeStore } from "@/store/todoBadge";

/** 체크를 누를 때 도는 순서. 빈칸 → 완료(v) → 실패(x) → 빈칸. */
const NEXT_STATUS: Record<TodoStatus, TodoStatus> = {
  pending: "done",
  done: "failed",
  failed: "pending",
};

const STATUS_LABEL: Record<TodoStatus, string> = {
  pending: "미완료",
  done: "완료",
  failed: "실패",
};

export function TodoPage() {
  const toast = useToast();
  const navigate = useNavigate();

  // 월간 보기 등에서 특정 날짜로 들어올 수 있게 ?date=YYYY-MM-DD 를 초기값으로 받는다.
  const [searchParams] = useSearchParams();
  const [date, setDate] = useState(() => {
    const q = searchParams.get("date");
    return q && /^\d{4}-\d{2}-\d{2}$/.test(q) ? q : todayStr();
  });
  const [day, setDayData] = useState<TodoDayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  /** 새 항목의 시작 시각 ("HH:MM"). null 이면 종일 — 기본값이다. */
  const [newTime, setNewTime] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draggingId, setDraggingId] = useState<number | null>(null);

  const load = useCallback(async (d: string) => {
    setLoading(true);
    setError(null);
    try {
      setDayData(await getDay(d));
    } catch (err) {
      setError(extractErrorMessage(err, "할 일을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(date);
  }, [load, date]);

  // 사이드 네비 "오늘 할 일" 배지 동기화 — 오늘 데이터를 들고 있는 동안은 체크/추가/삭제가
  // 반영된 카운트를 그대로 밀어 준다(store 가 오늘이 아니면 무시한다).
  const reportDay = useTodoBadgeStore((s) => s.reportDay);
  useEffect(() => {
    if (day) reportDay(day);
  }, [day, reportDay]);

  const isToday = date === todayStr();
  const items = day?.items ?? [];
  const done = day?.done ?? 0;
  // 달성률 분모는 그날의 전체 항목이다 — 실패(X)는 명시적 미달성이라 분모에서 빼지 않는다.
  const total = day?.total ?? 0;
  const ratio = total > 0 ? done / total : 0;

  const patchLocal = (id: number, next: Partial<TodoItem>) =>
    setDayData((prev) =>
      prev
        ? { ...prev, items: prev.items.map((it) => (it.id === id ? { ...it, ...next } : it)) }
        : prev,
    );

  const setStatus = async (item: TodoItem, status: TodoStatus) => {
    const prev = item.status;
    patchLocal(item.id, { status });
    try {
      await updateTodo(item.id, { status });
      // 카운트 재계산을 위해 조용히 새로고침(낙관적 표시는 이미 반영됨).
      setDayData((d) => (d ? recount(d) : d));
    } catch (err) {
      patchLocal(item.id, { status: prev });
      toast.error(extractErrorMessage(err, "상태 변경에 실패했습니다."));
    }
  };

  /** 체크 한 번에 상태를 한 칸씩 돌린다: 빈칸 → 완료(v) → 실패(x) → 빈칸. */
  const cycleStatus = (item: TodoItem) =>
    setStatus(item, NEXT_STATUS[item.status]);

  const add = async () => {
    const title = newTitle.trim();
    if (!title) return;
    setAdding(true);
    try {
      const created = await createTodo(date, title, newTime);
      setDayData((d) =>
        d ? recount({ ...d, items: sortItems([...d.items, created]) }) : d,
      );
      setNewTitle("");
      // 시각은 남겨 둔다 — 같은 시간대 할 일을 연달아 넣는 흐름이 흔하다.
    } catch (err) {
      toast.error(extractErrorMessage(err, "추가에 실패했습니다."));
    } finally {
      setAdding(false);
    }
  };

  /** 항목의 시작 시각 변경("HH:MM" 또는 null=종일). 저장 후 목록 순서를 다시 잡는다. */
  const setStartTime = async (item: TodoItem, next: string | null) => {
    const prev = day;
    try {
      const saved = await updateTodo(item.id, { start_time: next });
      setDayData((d) =>
        d
          ? {
              ...d,
              items: sortItems(d.items.map((it) => (it.id === saved.id ? saved : it))),
            }
          : d,
      );
    } catch (err) {
      setDayData(prev);
      toast.error(extractErrorMessage(err, "시각 변경에 실패했습니다."));
    }
  };

  const remove = async (item: TodoItem) => {
    const prev = day;
    setDayData((d) =>
      d ? recount({ ...d, items: d.items.filter((it) => it.id !== item.id) }) : d,
    );
    try {
      await deleteTodo(item.id);
    } catch (err) {
      setDayData(prev);
      toast.error(extractErrorMessage(err, "삭제에 실패했습니다."));
    }
  };

  // --- 드래그 정렬 ---------------------------------------------------------
  // 시각이 있는 항목은 시각이 순서를 정하므로 드래그 대상이 아니다. 종일 항목끼리만 실시간으로
  // 재배열하고, 놓을 때 바뀐 항목의 sort_order 만 저장한다. 종일 항목은 언제나 목록의 앞쪽
  // 연속 구간이라 그 안에서만 자리를 바꾸면 전체 순서가 깨지지 않는다.
  const onDragOverItem = (targetId: number) => {
    if (draggingId === null || draggingId === targetId) return;
    setDayData((prev) => {
      if (!prev) return prev;
      const list = [...prev.items];
      const from = list.findIndex((i) => i.id === draggingId);
      const to = list.findIndex((i) => i.id === targetId);
      if (from === -1 || to === -1 || from === to) return prev;
      // 시각이 붙은 항목은 집지도, 그 자리에 끼워 넣지도 않는다.
      if (list[from].start_time !== null || list[to].start_time !== null) return prev;
      const [moved] = list.splice(from, 1);
      list.splice(to, 0, moved);
      return { ...prev, items: list };
    });
  };

  const onDragEnd = async () => {
    const wasDragging = draggingId !== null;
    setDraggingId(null);
    if (!wasDragging) return;
    // 정규화 대상은 종일 항목뿐 — 화면 순서대로 sort_order 0..k-1 을 매기고 바뀐 것만 저장한다.
    const allDay = (day?.items ?? []).filter((it) => it.start_time === null);
    const orderOf = new Map(allDay.map((it, idx) => [it.id, idx]));
    const changed = allDay.filter((it, idx) => it.sort_order !== idx);
    if (changed.length === 0) return;
    setDayData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((it) =>
              orderOf.has(it.id) ? { ...it, sort_order: orderOf.get(it.id)! } : it,
            ),
          }
        : prev,
    );
    try {
      await Promise.all(
        changed.map((it) => updateTodo(it.id, { sort_order: orderOf.get(it.id)! })),
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, "정렬 저장에 실패했습니다."));
      void load(date);
    }
  };

  // 최신 핸들러를 ref 로 유지해 드래그 중 리스너 재구독 없이 최신 상태를 참조한다.
  const dragOverRef = useRef(onDragOverItem);
  dragOverRef.current = onDragOverItem;
  const dragEndRef = useRef(onDragEnd);
  dragEndRef.current = onDragEnd;

  // Pointer Events 로 마우스·터치·펜 통합 드래그. 핸들의 pointerdown 이 draggingId 를 세우면
  // 전역 pointermove 로 포인터 아래 항목을 찾아 실시간 재배열하고, pointerup 에서 저장한다.
  useEffect(() => {
    if (draggingId === null) return;
    const onMove = (e: PointerEvent) => {
      e.preventDefault(); // 터치 스크롤 방지
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const row = el?.closest<HTMLElement>("[data-todo-id]");
      if (!row) return;
      const targetId = Number(row.dataset.todoId);
      if (!Number.isNaN(targetId)) dragOverRef.current(targetId);
    };
    const onUp = () => void dragEndRef.current();
    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [draggingId]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                <span className="text-accent">
                  <CalendarIcon width={18} height={18} />
                </span>
                할 일
              </h1>
              <p className="mt-0.5 text-sm text-muted">
                매일의 할 일과 루틴을 관리하세요.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                className="btn btn-secondary"
                onClick={() => navigate("/todo/month")}
              >
                <CalendarIcon width={16} height={16} />
                월간
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => navigate("/routines")}
              >
                <RepeatIcon width={16} height={16} />
                루틴
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => navigate("/todo/reports")}
              >
                <ChartIcon width={16} height={16} />
                리포트
              </button>
            </div>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto w-full max-w-2xl">
          {/* 날짜 네비게이션 */}
          <div className="mb-4 flex items-center justify-between gap-3">
            <button
              className="btn btn-ghost px-2"
              aria-label="이전 날"
              onClick={() => setDate((d) => addDays(d, -1))}
            >
              <ChevronLeftIcon />
            </button>
            <div className="text-center">
              <div className="text-base font-semibold">{formatDayLabel(date)}</div>
              {!isToday && (
                <button
                  className="text-xs text-accent hover:underline"
                  onClick={() => setDate(todayStr())}
                >
                  오늘로 이동
                </button>
              )}
            </div>
            <button
              className="btn btn-ghost px-2"
              aria-label="다음 날"
              onClick={() => setDate((d) => addDays(d, 1))}
            >
              <ChevronRightIcon />
            </button>
          </div>

          {/* 진행률 */}
          {!loading && !error && (
            <div className="card mb-4 p-4">
              <div className="mb-1.5 flex items-center justify-between text-sm">
                <span className="font-medium">진행률</span>
                <span className="text-muted">
                  {done} / {total} 완료
                  {(day?.failed ?? 0) > 0 && ` · ${day?.failed} 실패`}
                </span>
              </div>
              <div className="h-2.5 w-full overflow-hidden rounded-full bg-muted-token">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.round(ratio * 100)}%`,
                    background: "var(--success)",
                  }}
                />
              </div>
            </div>
          )}

          {/* 추가 입력 — 제목 + 시작 시각(기본 종일). 좁은 화면에서는 시각 줄이 아래로 접힌다. */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <input
              className="input min-w-40 flex-1"
              placeholder="할 일을 추가하세요"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void add();
              }}
              maxLength={500}
            />
            <TimeSelect value={newTime} onChange={setNewTime} idPrefix="new-todo" />
            <button
              className="btn btn-primary"
              disabled={adding || !newTitle.trim()}
              onClick={() => void add()}
            >
              <PlusIcon width={16} height={16} />
              추가
            </button>
          </div>

          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void load(date)} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<CalendarIcon width={40} height={40} />}
              title="이 날의 할 일이 없습니다"
              hint="위에서 할 일을 추가하거나, 루틴을 만들어 매일 자동으로 채워보세요."
            />
          ) : (
            <ul
              className={`flex flex-col gap-2 ${draggingId !== null ? "select-none" : ""}`}
            >
              {items.map((item) => (
                <TodoRow
                  key={item.id}
                  item={item}
                  dragging={draggingId === item.id}
                  onDragStart={() => setDraggingId(item.id)}
                  onCycleStatus={() => void cycleStatus(item)}
                  // 루틴 파생 항목은 종일로 고정한다 — 삭제를 막는 것과 같은 선(임시 항목만 손댄다).
                  onSetStartTime={
                    item.routine_id === null
                      ? (next) => void setStartTime(item, next)
                      : undefined
                  }
                  onDelete={item.routine_id === null ? () => void remove(item) : undefined}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * 서버(get_day)와 같은 순서로 다시 세운다 — 종일 먼저, 그다음 시각순, 같은 칸 안에서는
 * 드래그 순서(sort_order), 마지막은 생성순(id). 항목을 추가하거나 시각을 바꾼 뒤 새로고침
 * 없이 제자리를 찾게 하려고 클라이언트에도 같은 규칙을 둔다.
 */
function sortItems(items: TodoItem[]): TodoItem[] {
  return [...items].sort((a, b) => {
    const at = a.start_time === null;
    const bt = b.start_time === null;
    if (at !== bt) return at ? -1 : 1;
    // 서버는 "HH:MM:SS", 낙관적 갱신은 "HH:MM" 이라 자릿수를 맞춰 비교한다.
    const av = formatTimeLabel(a.start_time);
    const bv = formatTimeLabel(b.start_time);
    if (av !== bv) return av < bv ? -1 : 1;
    if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
    return a.id - b.id;
  });
}

/**
 * 시작 시각 선택 — 10분 단위. "종일"이 기본이고, 시를 고르면 분 선택이 따라 나온다.
 * 144칸짜리 단일 드롭다운보다 시/분 두 칸이 훑기 쉽다.
 */
function TimeSelect({
  value,
  onChange,
  idPrefix,
}: {
  value: string | null;
  onChange: (next: string | null) => void;
  idPrefix: string;
}) {
  const t = parseTimeStr(value);
  return (
    <div className="flex items-center gap-1.5">
      <select
        id={`${idPrefix}-hour`}
        className="input w-auto"
        aria-label="시작 시각 (시)"
        value={t ? t.hour : ""}
        onChange={(e) =>
          onChange(
            e.target.value === ""
              ? null
              : toTimeStr(Number(e.target.value), t?.minute ?? 0),
          )
        }
      >
        <option value="">종일</option>
        {HOUR_OPTIONS.map((h) => (
          <option key={h} value={h}>
            {String(h).padStart(2, "0")}시
          </option>
        ))}
      </select>
      {t && (
        <select
          id={`${idPrefix}-minute`}
          className="input w-auto"
          aria-label="시작 시각 (분)"
          value={t.minute}
          onChange={(e) => onChange(toTimeStr(t.hour, Number(e.target.value)))}
        >
          {MINUTE_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {String(m).padStart(2, "0")}분
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

/** 응답의 요약 카운트를 items 기준으로 재계산한다(낙관적 갱신 후 배지 동기화). */
function recount(d: TodoDayResponse): TodoDayResponse {
  const done = d.items.filter((it) => it.status === "done").length;
  const failed = d.items.filter((it) => it.status === "failed").length;
  const pending = d.items.filter((it) => it.status === "pending").length;
  return { ...d, total: d.items.length, done, failed, pending };
}

function TodoRow({
  item,
  dragging,
  onDragStart,
  onCycleStatus,
  onSetStartTime,
  onDelete,
}: {
  item: TodoItem;
  dragging: boolean;
  onDragStart: () => void;
  onCycleStatus: () => void;
  /** 없으면 시각 고정(루틴 파생) — 종일로 남는다. */
  onSetStartTime?: (next: string | null) => void;
  onDelete?: () => void;
}) {
  const done = item.status === "done";
  const failed = item.status === "failed";
  const allDay = item.start_time === null;
  const timeLabel = formatTimeLabel(item.start_time);
  const [editingTime, setEditingTime] = useState(false);

  return (
    <li
      data-todo-id={item.id}
      className={`card flex items-center gap-2.5 px-3 py-2.5 transition-opacity ${
        dragging ? "opacity-40" : ""
      }`}
    >
      {/* 드래그 핸들 (마우스·터치 통합). touch-action:none 으로 드래그 중 스크롤을 막는다.
          시각이 있는 항목은 시각이 순서를 정하므로 핸들을 내리고, 자리만 비워 행 높이·정렬을
          유지한다. */}
      {allDay ? (
        <span
          className="shrink-0 cursor-grab touch-none text-muted active:cursor-grabbing"
          title="드래그하여 순서 변경"
          aria-label="순서 변경 핸들"
          onPointerDown={(e) => {
            e.preventDefault();
            onDragStart();
          }}
        >
          <GripIcon width={16} height={16} />
        </span>
      ) : (
        <span className="w-4 shrink-0" aria-hidden />
      )}

      {/* 상태 체크 — 누를 때마다 빈칸 → 완료(v) → 실패(x) → 빈칸 으로 돈다.
          미완료 상태는 채움 없이 테두리만으로 존재를 알리므로, 장식용인 --border-color 로는
          부족하다(카드 위 1.4:1). 컨트롤 경계에 요구되는 3:1 을 넘기려면 --text-secondary 를
          써야 한다 (4테마 최저 3.07:1). */}
      <button
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 transition-colors"
        style={{
          borderColor: done
            ? "var(--success)"
            : failed
              ? "var(--danger)"
              : "var(--text-secondary)",
          background: done ? "var(--success)" : failed ? "var(--danger)" : "transparent",
          color: done || failed ? "var(--bg-primary)" : "transparent",
        }}
        // 세 상태를 도는 버튼이라 aria-pressed(2상태)로는 표현되지 않는다. 현재 상태와 다음
        // 상태를 이름에 함께 담아 스크린리더가 "지금 무엇이고 누르면 무엇이 되는지"를 읽게 한다.
        aria-label={`상태: ${STATUS_LABEL[item.status]} (누르면 ${STATUS_LABEL[NEXT_STATUS[item.status]]})`}
        title={`${STATUS_LABEL[item.status]} — 누르면 ${STATUS_LABEL[NEXT_STATUS[item.status]]}`}
        onClick={onCycleStatus}
      >
        {failed ? <XIcon width={14} height={14} /> : <CheckIcon width={14} height={14} />}
      </button>

      {/* 시각 — tabular-nums 로 자릿수를 맞춰 세로로 줄이 선다. 종일이면 자리를 쓰지 않는다. */}
      {!allDay && (
        <span
          className={`shrink-0 text-sm tabular-nums ${done || failed ? "text-muted" : ""}`}
        >
          {timeLabel}
        </span>
      )}

      <div className="min-w-0 flex-1">
        <div
          className={`truncate text-sm ${
            done
              ? "text-muted line-through"
              : failed
                ? "text-muted line-through opacity-70"
                : ""
          }`}
        >
          {item.title}
        </div>
        {item.routine_id !== null && (
          <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-accent">
            <RepeatIcon width={11} height={11} />
            루틴
          </span>
        )}
      </div>

      {/* 시각 편집 — 열면 시/분 선택이 그 자리에 뜨고, 고르는 즉시 저장되며 목록이 다시 정렬된다. */}
      {onSetStartTime &&
        (editingTime ? (
          <div className="flex shrink-0 items-center gap-1.5">
            <TimeSelect
              value={item.start_time}
              onChange={(next) => onSetStartTime(next)}
              idPrefix={`todo-${item.id}`}
            />
            <button
              className="btn btn-ghost px-2 py-1 text-xs"
              onClick={() => setEditingTime(false)}
            >
              완료
            </button>
          </div>
        ) : (
          <button
            className="flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-muted transition-colors hover:text-[color:var(--accent)]"
            title={allDay ? "시작 시각 정하기" : `시작 시각 ${timeLabel} — 누르면 변경`}
            aria-label={
              allDay ? "시작 시각 정하기 (종일)" : `시작 시각 ${timeLabel} 변경`
            }
            onClick={() => setEditingTime(true)}
          >
            <ClockIcon width={15} height={15} />
          </button>
        ))}

      {onDelete && (
        <button
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:text-[color:var(--danger)]"
          title="삭제"
          aria-label="삭제"
          onClick={onDelete}
        >
          <TrashIcon width={15} height={15} />
        </button>
      )}
    </li>
  );
}
