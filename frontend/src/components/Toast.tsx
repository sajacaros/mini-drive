/** 간단한 토스트 알림 — zustand store + 루트에 렌더되는 Toaster. */

import { useEffect } from "react";
import { create } from "zustand";

type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastState {
  toasts: ToastItem[];
  push: (kind: ToastKind, message: string) => void;
  remove: (id: number) => void;
}

let seq = 0;

const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (kind, message) => {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }));
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 3500);
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** 컴포넌트에서 토스트를 띄우는 훅. */
export function useToast() {
  const push = useToastStore((s) => s.push);
  return {
    success: (msg: string) => push("success", msg),
    error: (msg: string) => push("error", msg),
    info: (msg: string) => push("info", msg),
  };
}

/** 훅 밖(예: axios 인터셉터)에서 토스트를 띄우는 명령형 헬퍼. */
export function showToast(kind: ToastKind, message: string): void {
  useToastStore.getState().push(kind, message);
}

const KIND_STYLE: Record<ToastKind, string> = {
  success: "border-l-[color:var(--success)]",
  error: "border-l-[color:var(--danger)]",
  info: "border-l-[color:var(--accent)]",
};

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const remove = useToastStore((s) => s.remove);

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-full max-w-sm flex-col gap-2">
      {toasts.map((t) => (
        <ToastCard key={t.id} item={t} onClose={() => remove(t.id)} />
      ))}
    </div>
  );
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  useEffect(() => {
    // 마운트 트랜지션 없이 즉시 표시 — 접근성상 스크린리더가 읽을 수 있게 role 지정.
  }, []);
  return (
    <div
      role="status"
      onClick={onClose}
      className={`card pointer-events-auto cursor-pointer border-l-4 px-4 py-3 text-sm ${KIND_STYLE[item.kind]}`}
    >
      {item.message}
    </div>
  );
}
