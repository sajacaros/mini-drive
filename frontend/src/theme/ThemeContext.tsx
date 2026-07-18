/** ThemeContext + useTheme() (PRD 3.1.4.3). 전역 테마 상태 + <html> 클래스 동기화. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  applyThemeClass,
  getInitialTheme,
  persistTheme,
  THEME_META,
  type Theme,
  type ThemeMode,
  type ThemeScheme,
} from "./theme";

interface ThemeContextValue {
  theme: Theme;
  mode: ThemeMode;
  scheme: ThemeScheme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);

  // 테마 변경 시 <html> 클래스 동기화 + localStorage 저장.
  useEffect(() => {
    applyThemeClass(theme);
    persistTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => setThemeState(next), []);

  const value = useMemo<ThemeContextValue>(() => {
    const meta = THEME_META[theme];
    return { theme, mode: meta.mode, scheme: meta.scheme, setTheme };
  }, [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme 는 ThemeProvider 안에서만 사용할 수 있습니다.");
  return ctx;
}
