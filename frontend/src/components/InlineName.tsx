/**
 * 목록/그리드에서 항목 이름을 제자리에서 고치는 입력 (탐색기·파인더 방식).
 *
 * 편집 진입은 세 가지 — F2, 행 액션의 "이름 변경" 아이콘, 그리고 "이미 선택된 항목의
 * 이름을 한 번 더 클릭". 마지막 경로는 첫 클릭을 더블클릭(=열기)과 공유하므로 바로
 * 진입하지 않고, CLICK_TO_RENAME_DELAY_MS 안에 두 번째 클릭이 오면 열기에 양보한다.
 *
 * 저장은 Enter 또는 포커스 이탈, 취소는 Esc. 확장자를 뺀 본체만 선택된 채로 열려
 * 이름만 바로 덮어쓸 수 있다.
 */

import { useEffect, useRef } from "react";
import type { MouseEvent } from "react";

import type { FileNode } from "@/api/types";
import { ROW_ACTION_PROPS } from "@/lib/rowOpen";

/** 이름 클릭 후 이 시간 안에 두 번째 클릭이 오면 "열기"로 보고 편집 진입을 취소한다. */
const CLICK_TO_RENAME_DELAY_MS = 500;

/** 선택된 항목의 이름을 한 번 더 클릭했을 때 편집으로 들어가는 핸들러 묶음. */
export function useClickToRename(o: { enabled: boolean; onStart: () => void }) {
  const timer = useRef<number | null>(null);
  const cancel = () => {
    if (timer.current === null) return;
    window.clearTimeout(timer.current);
    timer.current = null;
  };
  useEffect(() => cancel, []);

  return {
    onClick: (e: MouseEvent) => {
      cancel();
      // 키보드 Enter/Space 로 누른 click 은 detail=0 — 그건 "열기"라 편집으로 새면 안 된다.
      if (!o.enabled || e.detail === 0) return;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        o.onStart();
      }, CLICK_TO_RENAME_DELAY_MS);
    },
    onDoubleClick: cancel,
  };
}

export interface InlineNameInputProps {
  file: FileNode;
  /** 확정된 이름. 공백/원래 이름과 같은 경우의 처리는 호출부 몫이다. */
  onSubmit: (name: string) => void;
  onCancel: () => void;
  className?: string;
}

export function InlineNameInput({ file, onSubmit, onCancel, className }: InlineNameInputProps) {
  const ref = useRef<HTMLInputElement>(null);
  // Enter 로 저장한 직후 따라오는 blur 가 두 번째 저장이 되지 않도록 종료는 한 번만.
  const done = useRef(false);
  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
  };

  // 확장자를 뺀 본체만 선택해 둔다 — 확장자는 그대로 두고 이름만 갈아끼우는 게 대부분이다.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const dot = file.is_folder ? -1 : file.name.lastIndexOf(".");
    el.setSelectionRange(0, dot > 0 ? dot : file.name.length);
  }, [file.is_folder, file.name]);

  return (
    <input
      ref={ref}
      defaultValue={file.name}
      aria-label="이름"
      className={`input px-1.5 py-0.5 ${className ?? ""}`}
      // 행의 선택/열기 판정에서 빠지고, 드래그로 글자를 긁어도 행이 끌려가지 않게 한다.
      {...ROW_ACTION_PROPS}
      draggable={false}
      onDragStart={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        // 편집 중에는 F2 같은 페이지 단축키가 끼어들지 않게 한다.
        e.stopPropagation();
        if (e.key === "Enter") finish(() => onSubmit(e.currentTarget.value));
        else if (e.key === "Escape") finish(onCancel);
      }}
      onBlur={(e) => finish(() => onSubmit(e.target.value))}
    />
  );
}
