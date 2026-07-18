import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./index.css";
import { ThemeProvider } from "./theme/ThemeContext";
import { applyThemeClass, getInitialTheme } from "./theme/theme";

// 렌더 전에 테마 클래스를 먼저 적용해 초기 플래시(FOUC)를 방지한다.
applyThemeClass(getInitialTheme());

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("루트 엘리먼트(#root)를 찾을 수 없습니다.");
}

createRoot(rootElement).render(
  <StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
);
