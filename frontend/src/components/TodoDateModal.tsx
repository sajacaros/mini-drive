/**
 * 할 일의 날짜를 고르는 모달 — 옮기기와 복제가 같은 화면을 쓴다.
 *
 * 두 동작은 "어느 날짜로?"라는 질문이 똑같고 결과만 다르다(원본이 떠나느냐, 사본이 생기느냐).
 * 그래서 mode 로만 갈라 제목·확정 버튼 문구를 바꾸고 나머지는 공유한다.
 *
 * 빠른 선택 버튼은 원본 날짜를 기준으로 센다 — 어제 날짜를 열어 놓고 "내일"을 눌렀을 때
 * 오늘이 아니라 그 항목의 다음 날이 나오는 쪽이 덜 놀랍다.
 */

import { useState } from "react";

import type { TodoItem } from "@/api/types";
import { Modal } from "@/components/Modal";
import { addDays, addMonths, formatDayLabel } from "@/lib/localDate";

export type TodoDateMode = "move" | "duplicate";

interface TodoDateModalProps {
  /** 대상 항목. null 이면 닫힌 상태. */
  item: TodoItem | null;
  mode: TodoDateMode;
  onClose: () => void;
  /** 확정. 고른 날짜("YYYY-MM-DD")를 넘긴다. */
  onSubmit: (date: string) => Promise<void>;
}

export function TodoDateModal({ item, mode, onClose, onSubmit }: TodoDateModalProps) {
  if (!item) return null;
  // 대상이나 용도가 바뀌면 통째로 새로 마운트해 기본 날짜를 다시 잡는다 — effect 로 이미 그린
  // 상태를 되돌리는 것보다 key 로 갈아 끼우는 쪽이 단순하다.
  return (
    <TodoDateDialog
      key={`${item.id}-${mode}`}
      item={item}
      mode={mode}
      onClose={onClose}
      onSubmit={onSubmit}
    />
  );
}

function TodoDateDialog({
  item,
  mode,
  onClose,
  onSubmit,
}: TodoDateModalProps & { item: TodoItem }) {
  const moving = mode === "move";
  // 기본값 — 옮기기는 다음 날(미루는 게 대부분), 복제는 같은 날.
  const [date, setDate] = useState(() => (moving ? addDays(item.date, 1) : item.date));
  const [submitting, setSubmitting] = useState(false);

  // 같은 날로 '옮기는' 것은 아무 일도 아니다. 복제는 같은 날도 뜻이 있다(하루에 두 번 할 일).
  const noop = moving && date === item.date;
  const invalid = !/^\d{4}-\d{2}-\d{2}$/.test(date);

  const submit = async () => {
    setSubmitting(true);
    try {
      await onSubmit(date);
    } finally {
      setSubmitting(false);
    }
  };

  const QUICK: { label: string; value: string }[] = [
    { label: "내일", value: addDays(item.date, 1) },
    { label: "다음 주", value: addDays(item.date, 7) },
    { label: "다음 달", value: addMonths(item.date, 1) },
  ];

  return (
    <Modal
      open
      title={moving ? "할 일 옮기기" : "할 일 복제"}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>
            취소
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={submitting || noop || invalid}
          >
            {submitting ? "처리 중…" : moving ? "옮기기" : "복제"}
          </button>
        </>
      }
    >
      <p className="mb-4 truncate text-sm font-medium">{item.title}</p>

      <label className="mb-1.5 block text-sm text-muted" htmlFor="todo-date-target">
        날짜
      </label>
      <input
        id="todo-date-target"
        type="date"
        className="input w-full"
        value={date}
        onChange={(e) => setDate(e.target.value)}
      />

      <div className="mt-2 flex flex-wrap gap-1.5">
        {QUICK.map((q) => (
          <button
            key={q.label}
            className={`btn btn-ghost px-2 py-1 text-xs ${
              date === q.value ? "text-accent" : ""
            }`}
            onClick={() => setDate(q.value)}
          >
            {q.label}
          </button>
        ))}
      </div>

      {!invalid && (
        <p className="mt-3 text-xs text-muted">
          {noop
            ? "이미 이 날짜에 있습니다."
            : `${formatDayLabel(date)}${moving ? "로 옮깁니다." : "에 사본을 만듭니다."}`}
        </p>
      )}
      {item.routine_id !== null && !moving && (
        <p className="mt-1 text-xs text-muted">
          사본은 루틴과 이어지지 않은 일반 할 일이 됩니다.
        </p>
      )}
    </Modal>
  );
}
