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
      {/* basename: 서브패스 배포(예 /drive) 지원. 런타임 치환된 BASE_URL 을 그대로 쓴다("/" 면 무효과). */}
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
);
