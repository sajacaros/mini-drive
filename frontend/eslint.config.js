// ESLint flat config (v9+). `npm run lint` 이 이 파일 없이 돌던 시절엔 명령이 통째로 실패했고,
// 그래서 프론트엔드에는 사실상 린트가 없었다.
//
// 규칙은 "타입 검사가 이미 잡는 것"을 제외하고 고른다 — tsc 가 strict + noUnusedLocals 로
// 돌고 있으므로(tsconfig.json) 미사용 변수·타입 오류를 여기서 또 볼 이유가 없다. 남는 값은
// 타입 검사가 못 보는 것들이다: 훅 규칙, Fast Refresh 경계, 실수하기 쉬운 JS 관용구.

import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // 빌드 산출물·의존성·Playwright 결과물은 검사 대상이 아니다.
    ignores: ["dist/**", "node_modules/**", "test-results/**", "playwright-report/**"],
  },

  // ── 애플리케이션 소스 (브라우저) ─────────────────────
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Fast Refresh 는 파일이 컴포넌트만 내보낼 때 동작한다. 상수를 곁들여 내보내는 건
      // 흔하고 무해하므로 허용하되, 그 외의 혼합은 경고로 남긴다.
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],

      // tsc 의 noUnusedLocals 와 중복이라 끈다 — 같은 문제를 두 도구가 다르게 보고하면
      // 어느 쪽을 고쳐야 하는지가 흐려진다.
      "@typescript-eslint/no-unused-vars": "off",

      "no-empty": ["error", { allowEmptyCatch: true }],

      // ── 아래 둘은 의도적으로 warn 이다 (2026-08-07) ──────────────────────
      // eslint-plugin-react-hooks v7 이 React Compiler 기준으로 새로 넣은 규칙들이고,
      // 지금 코드베이스에서 set-state-in-effect 44건 / refs 10건이 걸린다. 전부 "effect 에서
      // fetch → setState" 라는 한 가지 데이터 로딩 패턴에서 나오므로, 고치려면 페이지 여러
      // 개의 로딩 구조를 손봐야 한다(refs 10건 중 7건은 FileBrowserPage 분해와 같은 작업이다).
      //
      // error 로 두면 `npm run lint` 이 첫날부터 56개 에러를 뱉고, 그러면 아무도 안 본다 —
      // 린트가 없던 상태와 실질적으로 같아진다. warn 으로 두면 명령은 통과하되 기존 부채가
      // 눈에 남고, **나머지 규칙의 신규 위반은 error 로 CI 를 세운다**. 부채를 갚고 나면
      // 이 블록을 지우면 된다.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
    },
  },

  // ── E2E (Playwright, Node 런타임) ────────────────────
  {
    files: ["e2e/**/*.ts", "*.config.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.node, ...globals.browser },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": "off",
      // page.evaluate 안에서 브라우저 전역을 다루느라 any 가 불가피한 자리가 있다.
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
);
