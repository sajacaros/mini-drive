/** 테마 선택 — 2×2 매트릭스 미니 프리뷰 카드 팝오버 (PRD 3.1.4.1). */

import { useEffect, useRef, useState } from "react";

import { useTheme } from "@/theme/ThemeContext";
import { THEMES, THEME_META, type Theme } from "@/theme/theme";

/** 현재 테마의 대표색을 보여주는 작은 팔레트 스와치. */
function PreviewSwatch({ id }: { id: Theme }) {
  const p = THEME_META[id].preview;
  return (
    <div
      className="flex h-9 w-full items-center gap-1 rounded-md p-1.5"
      style={{ background: p.bg, border: `1px solid ${p.surface}` }}
    >
      <span className="h-full w-1.5 rounded-sm" style={{ background: p.surface }} />
      <span className="text-[10px] font-bold" style={{ color: p.text }}>
        Aa
      </span>
      <span className="ml-auto h-4 w-4 rounded-sm" style={{ background: p.accent }} />
    </div>
  );
}

export function ThemePicker() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        className="btn btn-secondary w-full justify-between"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2">
          <span
            className="h-4 w-4 rounded-full"
            style={{ background: THEME_META[theme].preview.accent }}
          />
          테마
        </span>
        <span className="text-xs text-muted">{THEME_META[theme].label}</span>
      </button>

      {open && (
        <div className="card absolute bottom-full left-0 z-30 mb-2 w-full min-w-56 p-2">
          <div className="grid grid-cols-2 gap-2">
            {THEMES.map((id) => {
              const active = id === theme;
              return (
                <button
                  key={id}
                  onClick={() => {
                    setTheme(id);
                    setOpen(false);
                  }}
                  className="flex flex-col gap-1.5 rounded-lg p-2 text-left transition-colors"
                  style={{
                    border: `2px solid ${active ? "var(--accent)" : "var(--border-color)"}`,
                    background: active ? "var(--bg-muted)" : "transparent",
                  }}
                  aria-pressed={active}
                >
                  <PreviewSwatch id={id} />
                  <span className="text-xs font-medium">{THEME_META[id].label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
