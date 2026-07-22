/**
 * 데일리 투두 — 날짜별 할 일. 날짜를 이동하며 조회하고(자정이 지나 새 날을 열면 활성 루틴이
 * 서버에서 자동 물질화된다), 각 항목을 체크(done)/건너뜀(skipped)으로 토글한다. 임시 항목은
 * 그날 직접 추가/삭제할 수 있고, 루틴 파생 항목은 배지로 구분해 X(건너뜀)로만 처리한다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

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
  GripIcon,
  PlusIcon,
  RepeatIcon,
  TrashIcon,
  XIcon,
} from "@/components/icons";
import { addDays, formatDayLabel, todayStr } from "@/lib/localDate";

export function TodoPage() {
  const toast = useToast();
  const navigate = useNavigate();

  const [date, setDate] = useState(() => todayStr());
  const [day, setDayData] = useState<TodoDayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
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

  const isToday = date === todayStr();
  const items = day?.items ?? [];
  const done = day?.done ?? 0;
  const actionable = (day?.done ?? 0) + (day?.pending ?? 0);
  const ratio = actionable > 0 ? done / actionable : 0;

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

  const toggleDone = (item: TodoItem) =>
    setStatus(item, item.status === "done" ? "pending" : "done");
  const toggleSkip = (item: TodoItem) =>
    setStatus(item, item.status === "skipped" ? "pending" : "skipped");

  const add = async () => {
    const title = newTitle.trim();
    if (!title) return;
    setAdding(true);
    try {
      const created = await createTodo(date, title);
      setDayData((d) =>
        d ? recount({ ...d, items: [...d.items, created] }) : d,
      );
      setNewTitle("");
    } catch (err) {
      toast.error(extractErrorMessage(err, "추가에 실패했습니다."));
    } finally {
      setAdding(false);
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
  // 드래그하는 동안 로컬 목록을 실시간으로 재배열하고, 놓을 때 바뀐 항목의 sort_order 만 저장한다.
  const onDragOverItem = (targetId: number) => {
    if (draggingId === null || draggingId === targetId) return;
    setDayData((prev) => {
      if (!prev) return prev;
      const list = [...prev.items];
      const from = list.findIndex((i) => i.id === draggingId);
      const to = list.findIndex((i) => i.id === targetId);
      if (from === -1 || to === -1 || from === to) return prev;
      const [moved] = list.splice(from, 1);
      list.splice(to, 0, moved);
      return { ...prev, items: list };
    });
  };

  const onDragEnd = async () => {
    const wasDragging = draggingId !== null;
    setDraggingId(null);
    if (!wasDragging) return;
    const list = day?.items ?? [];
    // 현재 화면 순서를 sort_order 0..n-1 로 정규화하되, 실제로 바뀐 항목만 저장한다.
    const changed = list.filter((it, idx) => it.sort_order !== idx);
    if (changed.length === 0) return;
    setDayData((prev) =>
      prev
        ? { ...prev, items: prev.items.map((it, idx) => ({ ...it, sort_order: idx })) }
        : prev,
    );
    try {
      await Promise.all(changed.map((it) => updateTodo(it.id, { sort_order: list.indexOf(it) })));
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
                  {done} / {actionable} 완료
                  {(day?.skipped ?? 0) > 0 && ` · ${day?.skipped} 건너뜀`}
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

          {/* 추가 입력 */}
          <div className="mb-4 flex items-center gap-2">
            <input
              className="input flex-1"
              placeholder="할 일을 추가하세요"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void add();
              }}
              maxLength={500}
            />
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
                  onToggleDone={() => void toggleDone(item)}
                  onToggleSkip={() => void toggleSkip(item)}
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

/** 응답의 요약 카운트를 items 기준으로 재계산한다(낙관적 갱신 후 배지 동기화). */
function recount(d: TodoDayResponse): TodoDayResponse {
  const done = d.items.filter((it) => it.status === "done").length;
  const skipped = d.items.filter((it) => it.status === "skipped").length;
  const pending = d.items.filter((it) => it.status === "pending").length;
  return { ...d, total: d.items.length, done, skipped, pending };
}

function TodoRow({
  item,
  dragging,
  onDragStart,
  onToggleDone,
  onToggleSkip,
  onDelete,
}: {
  item: TodoItem;
  dragging: boolean;
  onDragStart: () => void;
  onToggleDone: () => void;
  onToggleSkip: () => void;
  onDelete?: () => void;
}) {
  const done = item.status === "done";
  const skipped = item.status === "skipped";
  return (
    <li
      data-todo-id={item.id}
      className={`card flex items-center gap-2.5 px-3 py-2.5 transition-opacity ${
        dragging ? "opacity-40" : ""
      }`}
    >
      {/* 드래그 핸들 (마우스·터치 통합). touch-action:none 으로 드래그 중 스크롤을 막는다. */}
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

      {/* 체크 (완료) — 미완료 상태는 채움 없이 테두리만으로 존재를 알리므로, 장식용인
          --border-color 로는 부족하다(카드 위 1.4:1). 컨트롤 경계에 요구되는 3:1 을
          넘기려면 --text-secondary 를 써야 한다 (4테마 최저 3.07:1). */}
      <button
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 transition-colors"
        style={{
          borderColor: done ? "var(--success)" : "var(--text-secondary)",
          background: done ? "var(--success)" : "transparent",
          color: done ? "var(--bg-primary)" : "transparent",
        }}
        aria-label={done ? "완료 해제" : "완료"}
        aria-pressed={done}
        onClick={onToggleDone}
      >
        <CheckIcon width={14} height={14} />
      </button>

      <div className="min-w-0 flex-1">
        <div
          className={`truncate text-sm ${
            done
              ? "text-muted line-through"
              : skipped
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

      {/* 건너뜀 (X) */}
      <button
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors"
        style={{
          color: skipped ? "var(--danger)" : "var(--text-secondary)",
          background: skipped ? "color-mix(in srgb, var(--danger) 16%, transparent)" : "transparent",
        }}
        title={skipped ? "건너뜀 해제" : "오늘은 건너뜀"}
        aria-label={skipped ? "건너뜀 해제" : "건너뜀"}
        aria-pressed={skipped}
        onClick={onToggleSkip}
      >
        <XIcon width={15} height={15} />
      </button>

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
