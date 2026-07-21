/** 테마 정의·저장 (PRD 3.1.4). 클래스명은 3.1.4.3 규격: theme-{mode}-{scheme}. */

export type ThemeMode = "modern" | "gameboy";
export type ThemeScheme = "dark" | "light";
export type Theme = `${ThemeMode}-${ThemeScheme}`;

export const THEMES: Theme[] = ["modern-dark", "modern-light", "gameboy-dark", "gameboy-light"];

const STORAGE_KEY = "minidrive-theme";

export interface ThemeMeta {
  id: Theme;
  label: string;
  mode: ThemeMode;
  scheme: ThemeScheme;
  /** 프리뷰 카드용 대표 색 (PRD 3.1.4.2 토큰) */
  preview: { bg: string; surface: string; accent: string; text: string };
}

export const THEME_META: Record<Theme, ThemeMeta> = {
  "modern-dark": {
    id: "modern-dark",
    label: "모던 다크",
    mode: "modern",
    scheme: "dark",
    preview: { bg: "#0f172a", surface: "#1e293b", accent: "#3b82f6", text: "#f1f5f9" },
  },
  "modern-light": {
    id: "modern-light",
    label: "모던 라이트",
    mode: "modern",
    scheme: "light",
    preview: { bg: "#f1f5f9", surface: "#ffffff", accent: "#3b82f6", text: "#0f172a" },
  },
  "gameboy-dark": {
    id: "gameboy-dark",
    label: "게임보이 다크",
    mode: "gameboy",
    scheme: "dark",
    preview: { bg: "#0a0a0a", surface: "#1a1a1a", accent: "#00ff41", text: "#00ff41" },
  },
  "gameboy-light": {
    id: "gameboy-light",
    label: "게임보이 라이트",
    mode: "gameboy",
    scheme: "light",
    preview: { bg: "#f0ead6", surface: "#d4c9a8", accent: "#8bac0f", text: "#303030" },
  },
};

function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && (THEMES as string[]).includes(value);
}

/** localStorage 오버라이드 → 없으면 prefers-color-scheme 로 모던 다크/라이트 자동 (PRD P2). */
export function getInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isTheme(saved)) return saved;
  } catch {
    // 스토리지 접근 불가 시 시스템 선호도로 폴백
  }
  const prefersDark =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  return prefersDark ? "modern-dark" : "modern-light";
}

/** <html> 에 테마 클래스 토글 (한 번에 하나만). */
export function applyThemeClass(theme: Theme): void {
  const root = document.documentElement;
  root.classList.remove(...THEMES.map((t) => `theme-${t}`));
  root.classList.add(`theme-${theme}`);
}

export function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // 저장 실패는 무시 (세션 한정으로 동작)
  }
}
