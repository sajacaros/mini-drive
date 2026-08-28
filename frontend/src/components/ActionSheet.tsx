/**
 * 하단 액션 시트 — 좁은 화면에서 "행 액션 아이콘 줄"을 대신한다.
 *
 * 목록의 한 항목에 걸린 동작은 최대 아홉 가지(미리보기·다운로드·새 버전·버전 기록·공유·
 * 권한·위키·이동·이름 변경·삭제)나 되는데, 휴대폰에서 이걸 28px 아이콘으로 늘어놓으면
 * 카드 안에서 세 줄로 접히고 무엇이 무엇인지도 알 수 없다. 그래서 목록에는 `⋯` 하나만 두고,
 * 누르면 **라벨이 붙은 세로 목록**을 아래에서 올린다 — 아이콘 툴팁은 손가락으로 볼 수 없다.
 *
 * 동작 목록 자체는 호출부가 만든다(FileBrowserPage 의 buildRowActions). 이 컴포넌트는 표시와
 * 닫힘만 맡아, 권한 판정이 데스크톱 아이콘 줄과 갈라지지 않게 한다.
 */

import { useEffect, type ReactNode } from "react";

export interface SheetAction {
  /** React key 이자 테스트 훅. */
  key: string;
  label: string;
  icon: ReactNode;
  onClick: () => void;
  /** 파괴적 동작(삭제) — 빨갛게 칠하고 목록 끝에 둔다. */
  danger?: boolean;
}

export function ActionSheet({
  open,
  title,
  actions,
  onClose,
}: {
  open: boolean;
  /** 시트 상단에 보여줄 대상 이름(어느 항목을 조작하는지). */
  title: string;
  actions: SheetAction[];
  onClose: () => void;
}) {
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
      className="fixed inset-0 z-50 flex items-end bg-black/40"
      onMouseDown={onClose}
      role="presentation"
    >
      <div
        className="card max-h-[80dvh] w-full overflow-y-auto rounded-b-none p-0"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${title} 작업`}
      >
        <p className="truncate border-b border-token px-4 py-3 text-sm font-medium" title={title}>
          {title}
        </p>
        <ul className="p-2">
          {actions.map((a) => (
            <li key={a.key}>
              <button
                data-action={a.key}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-sm font-medium transition-colors hover:bg-[color:var(--bg-muted)] ${
                  a.danger ? "text-danger" : "text-[color:var(--text-primary)]"
                }`}
                onClick={() => {
                  // 먼저 닫는다 — 동작이 모달을 열면(공유·권한 등) 시트가 그 위에 남는다.
                  onClose();
                  a.onClick();
                }}
              >
                <span className="shrink-0 text-muted">{a.icon}</span>
                {a.label}
              </button>
            </li>
          ))}
        </ul>
        {/* 홈 인디케이터가 있는 기기에서 마지막 항목이 가리지 않도록 여백을 남긴다. */}
        <div className="h-[env(safe-area-inset-bottom)]" />
      </div>
    </div>
  );
}
