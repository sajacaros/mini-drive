import { defineConfig, devices } from "@playwright/test";

/**
 * E2E 통합 테스트 (Phase 8 드라이브 UX 검증).
 * 대상: docker compose 로 기동한 mini-drive 전체 스택 (nginx 게이트웨이 :80).
 * Playwright test agents(planner/generator/healer)가 이 설정으로 seed 프로젝트를 찾는다.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  // 정리(finally) 로직이 "존재하지 않음"을 확정하기 위해 각 항목마다 최대 5s씩 대기할 수 있어
  // (isVisible() 즉시 판정 대신 waitFor 기반 existsNow 사용, 레이스 방지) 기본 30s로는 빠듯하다.
  timeout: 60_000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    // 전역 인증: 폼 로그인 1회 → storageState 저장. 아래 chromium 이 이걸 먼저 실행한다.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      testIgnore: /auth\.setup\.ts/,
      dependencies: ["setup"],
      use: {
        ...devices["Desktop Chrome"],
        // setup 이 저장한 인증 상태(localStorage 토큰 포함)를 모든 테스트가 재사용.
        storageState: "e2e/.auth/admin.json",
      },
    },
  ],
});
