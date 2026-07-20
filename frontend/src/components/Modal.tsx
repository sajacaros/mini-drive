/** 모달 다이얼로그 — 오버레이 + 중앙 카드. Esc/오버레이 클릭으로 닫힌다. */

import { useEffect, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** 카드 최대 너비. 기본 md. */
  size?: "md" | "lg";
}

const SIZE_CLASS: Record<NonNullable<ModalProps["size"]>, string> = {
  md: "max-w-md",
  lg: "max-w-lg",
};

export function Modal({ open, title, onClose, children, footer, size = "md" }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={onClose}
    >
      <div
        className={`card flex max-h-[90vh] w-full flex-col ${SIZE_CLASS[size]} p-5`}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-4 flex shrink-0 items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button className="btn btn-ghost px-2 py-1" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>
        <div className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1">{children}</div>
        {footer && <div className="mt-5 flex shrink-0 justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}
