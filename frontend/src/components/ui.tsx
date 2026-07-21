/** 공용 UI 조각 — 스피너, 상태(로딩/빈/오류) 표시. */

import type { ReactNode } from "react";

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.2" strokeWidth="4" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

export function LoadingState({ label = "불러오는 중..." }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted">
      <Spinner className="text-accent" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function EmptyState({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      {icon && <div className="text-4xl opacity-40">{icon}</div>}
      <p className="font-medium">{title}</p>
      {hint && <p className="text-sm text-muted">{hint}</p>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <p className="text-sm" style={{ color: "var(--danger)" }}>
        {message}
      </p>
      {onRetry && (
        <button className="btn btn-secondary" onClick={onRetry}>
          다시 시도
        </button>
      )}
    </div>
  );
}

/** 페이지네이션 컨트롤 (이전/다음). totalPages<=1 이면 렌더하지 않는다. */
export function Pagination({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="mt-4 flex items-center justify-center gap-3 text-sm">
      <button className="btn btn-secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        이전
      </button>
      <span className="text-muted">
        {page} / {totalPages}
      </span>
      <button
        className="btn btn-secondary"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
      >
        다음
      </button>
    </div>
  );
}

/** 상태 배지 (사용자 상태, 공유 활성 여부 등). */
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger" | "accent";
}) {
  // 테마 토큰 기반 — 4테마 모두에서 배경/텍스트가 일관되게 대비되도록 color-mix 로 옅은 배경을 만든다.
  const base: Record<string, string> = {
    neutral: "var(--text-secondary)",
    success: "var(--success)",
    warning: "var(--warning)",
    danger: "var(--danger)",
    accent: "var(--accent)",
  };
  const color = base[tone];
  // 글씨는 톤 색을 그대로 쓰지 않고 text-primary 쪽으로 당긴다. 톤 색을 그대로 쓰면 같은 색의
  // 옅은 틴트 위에 얹히는 꼴이라 4테마 전부에서 2.2~3.2:1 로 AA 에 못 미쳤다. 4테마 × 5톤 ×
  // 2표면을 훑어 4.5:1 을 넘는 지점이 톤 25% (최저 4.88:1, 병목은 게임보이 라이트 accent).
  // 색은 칩 배경이 담당하고 글씨는 hue 만 남긴다.
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
      style={{
        color: `color-mix(in srgb, ${color} 25%, var(--text-primary))`,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
      }}
    >
      {children}
    </span>
  );
}
