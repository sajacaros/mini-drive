/**
 * 반복 루틴 관리 — "매일 운동" 같은 습관 템플릿. 매일/특정 요일 주기를 지정하면 해당 날짜의
 * 투두를 열 때 자동으로 채워진다(물질화). 비활성화하면 이후 날짜에는 더 채워지지 않고, 삭제하면
 * 과거 파생 항목은 임시 항목으로 남아 기록이 보존된다.
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import {
  createRoutine,
  deleteRoutine,
  listRoutines,
  updateRoutine,
} from "@/api/todos";
import type { Routine, RoutineFrequency } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import {
  CalendarIcon,
  PlusIcon,
  RenameIcon,
  RepeatIcon,
  TrashIcon,
} from "@/components/icons";
import { WEEKDAY_LABELS } from "@/lib/localDate";

interface EditState {
  id: number | null; // null = 새로 만들기
  title: string;
  frequency: RoutineFrequency;
  days: number[];
}

const EMPTY_EDIT: EditState = {
  id: null,
  title: "",
  frequency: "daily",
  days: [],
};

export function RoutinesPage() {
  const toast = useToast();

  const [routines, setRoutines] = useState<Routine[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRoutines((await listRoutines()).items);
    } catch (err) {
      setError(extractErrorMessage(err, "루틴을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => setEdit({ ...EMPTY_EDIT });
  const openEdit = (r: Routine) =>
    setEdit({ id: r.id, title: r.title, frequency: r.frequency, days: r.days_of_week });

  const save = async () => {
    if (!edit) return;
    const title = edit.title.trim();
    if (!title) {
      toast.error("제목을 입력하세요.");
      return;
    }
    if (edit.frequency === "weekly" && edit.days.length === 0) {
      toast.error("주간 루틴은 요일을 1개 이상 선택하세요.");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        title,
        frequency: edit.frequency,
        days_of_week: edit.frequency === "weekly" ? edit.days : [],
      };
      if (edit.id === null) {
        await createRoutine(payload);
        toast.success("루틴을 만들었습니다.");
      } else {
        await updateRoutine(edit.id, payload);
        toast.success("루틴을 수정했습니다.");
      }
      setEdit(null);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "저장에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (r: Routine) => {
    const prev = routines;
    setRoutines((list) =>
      list.map((it) => (it.id === r.id ? { ...it, is_active: !it.is_active } : it)),
    );
    try {
      await updateRoutine(r.id, { is_active: !r.is_active });
    } catch (err) {
      setRoutines(prev);
      toast.error(extractErrorMessage(err, "상태 변경에 실패했습니다."));
    }
  };

  const remove = async (r: Routine) => {
    if (!window.confirm(`루틴 "${r.title}"을(를) 삭제할까요?\n과거 기록은 유지됩니다.`)) return;
    const prev = routines;
    setRoutines((list) => list.filter((it) => it.id !== r.id));
    try {
      await deleteRoutine(r.id);
      toast.success("루틴을 삭제했습니다.");
    } catch (err) {
      setRoutines(prev);
      toast.error(extractErrorMessage(err, "삭제에 실패했습니다."));
    }
  };

  const toggleDay = (d: number) =>
    setEdit((e) =>
      e
        ? {
            ...e,
            days: e.days.includes(d)
              ? e.days.filter((x) => x !== d)
              : [...e.days, d].sort((a, b) => a - b),
          }
        : e,
    );

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <div className="flex items-center justify-between gap-4">
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                <span className="text-accent">
                  <RepeatIcon width={18} height={18} />
                </span>
                반복 루틴
              </h1>
              <p className="mt-0.5 text-sm text-muted">
                매일 이어갈 습관을 등록하면 매일 자동으로 할 일에 나타납니다.
              </p>
            </div>
            <button className="btn btn-primary" onClick={openCreate}>
              <PlusIcon width={16} height={16} />
              루틴 추가
            </button>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto w-full max-w-2xl">
          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void load()} />
          ) : routines.length === 0 ? (
            <EmptyState
              icon={<RepeatIcon width={40} height={40} />}
              title="등록된 루틴이 없습니다"
              hint="'루틴 추가'로 매일 또는 특정 요일에 반복할 습관을 만들어 보세요."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {routines.map((r) => (
                <li
                  key={r.id}
                  className={`card flex items-center gap-3 px-4 py-3 ${
                    r.is_active ? "" : "opacity-60"
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{r.title}</span>
                      {!r.is_active && <Badge tone="neutral">비활성</Badge>}
                    </div>
                    <div className="mt-1">
                      {r.frequency === "daily" ? (
                        <Badge tone="accent">매일</Badge>
                      ) : (
                        <span className="inline-flex gap-1">
                          {r.days_of_week.map((d) => (
                            <span
                              key={d}
                              className="inline-flex h-5 w-5 items-center justify-center rounded text-xs font-medium"
                              style={{
                                // Badge 와 같은 틴트 칩 — 글씨를 text-primary 쪽으로 당겨야 AA 를 넘는다.
                                color:
                                  "color-mix(in srgb, var(--accent) 25%, var(--text-primary))",
                                background:
                                  "color-mix(in srgb, var(--accent) 16%, transparent)",
                              }}
                            >
                              {WEEKDAY_LABELS[d]}
                            </span>
                          ))}
                        </span>
                      )}
                    </div>
                  </div>

                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
                    <input
                      type="checkbox"
                      checked={r.is_active}
                      onChange={() => void toggleActive(r)}
                    />
                    활성
                  </label>
                  <button
                    className="flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:text-accent"
                    title="수정"
                    aria-label="수정"
                    onClick={() => openEdit(r)}
                  >
                    <RenameIcon width={16} height={16} />
                  </button>
                  <button
                    className="flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:text-[color:var(--danger)]"
                    title="삭제"
                    aria-label="삭제"
                    onClick={() => void remove(r)}
                  >
                    <TrashIcon width={16} height={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <Modal
        open={edit !== null}
        title={edit?.id === null ? "루틴 추가" : "루틴 수정"}
        onClose={() => (saving ? undefined : setEdit(null))}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setEdit(null)} disabled={saving}>
              취소
            </button>
            <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
              저장
            </button>
          </>
        }
      >
        {edit && (
          <div className="flex flex-col gap-4">
            <div>
              <label className="mb-1 block text-sm font-medium">제목</label>
              <input
                className="input w-full"
                placeholder="예: 매일 운동 30분"
                value={edit.title}
                onChange={(e) => setEdit({ ...edit, title: e.target.value })}
                maxLength={500}
                autoFocus
              />
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium">주기</label>
              <div className="flex gap-2">
                <FreqButton
                  active={edit.frequency === "daily"}
                  label="매일"
                  icon={<CalendarIcon width={15} height={15} />}
                  onClick={() => setEdit({ ...edit, frequency: "daily" })}
                />
                <FreqButton
                  active={edit.frequency === "weekly"}
                  label="특정 요일"
                  icon={<RepeatIcon width={15} height={15} />}
                  onClick={() => setEdit({ ...edit, frequency: "weekly" })}
                />
              </div>
            </div>

            {edit.frequency === "weekly" && (
              <div>
                <label className="mb-1.5 block text-sm font-medium">요일 선택</label>
                <div className="flex gap-1.5">
                  {WEEKDAY_LABELS.map((label, d) => {
                    const on = edit.days.includes(d);
                    return (
                      <button
                        key={d}
                        type="button"
                        className="flex h-9 w-9 items-center justify-center rounded-full border text-sm font-medium transition-colors"
                        style={{
                          borderColor: on ? "var(--accent)" : "var(--border-color)",
                          background: on ? "var(--accent)" : "transparent",
                          color: on ? "var(--accent-fg)" : "var(--text-secondary)",
                        }}
                        onClick={() => toggleDay(d)}
                      >
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

function FreqButton({
  active,
  label,
  icon,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border py-2 text-sm font-medium transition-colors"
      style={{
        borderColor: active ? "var(--accent)" : "var(--border-color)",
        background: active ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "transparent",
        color: active ? "var(--accent)" : "var(--text-secondary)",
      }}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}
