import { defineConfig, devices } from "@playwright/test";

/**
 * E2E 통합 테스트 (Phase 8 드라이브 UX 검증).
 * 대상: docker compose 로 기동한 mini-drive 전체 스택 (nginx 게이트웨이 :7400).
 * Playwright test agents(planner/generator/healer)가 이 설정으로 seed 프로젝트를 찾는다.
 */
export default defineConfig({
  testDir: "./e2e",
  /*
    실행 동안만 백엔드 rate limiting 을 끄고 끝나면 원복한다(e2e/support/stack.ts).
    한 계정이 짧은 시간에 업로드를 반복하는 테스트가 여럿이라 분당 한도(10)에 걸리는데,
    걸리면 429 가 "완료 토스트 없음"으로 나타나 코드 회귀와 구분되지 않는다.
  */
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  // 정리(finally) 로직이 "존재하지 않음"을 확정하기 위해 각 항목마다 최대 5s씩 대기할 수 있어
  // (isVisible() 즉시 판정 대신 waitFor 기반 existsNow 사용, 레이스 방지) 기본 30s로는 빠듯하다.
  timeout: 60_000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    // 로컬 게이트웨이 기본 포트(GATEWAY_HTTP_PORT=7400). 다른 포트로 띄웠으면 E2E_BASE_URL 로 덮는다.
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:7400",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    // 전역 인증: 폼 로그인 1회 → storageState 저장. 아래 chromium 이 이걸 먼저 실행한다.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      // 모바일 전용 스펙은 아래 mobile 프로젝트가 자기 뷰포트로 따로 돌린다.
      testIgnore: [/auth\.setup\.ts/, /e2e\/mobile\//],
      dependencies: ["setup"],
      use: {
        ...devices["Desktop Chrome"],
        // setup 이 저장한 인증 상태(localStorage 토큰 포함)를 모든 테스트가 재사용.
        storageState: "e2e/.auth/admin.json",
      },
    },
    /*
      휴대폰 뷰포트(390×844, 터치). 데스크톱에서는 멀쩡한데 휴대폰에서만 깨지는 것들 —
      hover 로만 나타나는 액션, 화면 밖으로 밀리는 표 — 을 잡는다. 데스크톱 크로미움으로는
      hover 가 늘 있으므로 그 회귀를 영영 볼 수 없다.
    */
    {
      name: "mobile",
      testMatch: /e2e\/mobile\/.*\.spec\.ts/,
      dependencies: ["setup"],
      use: {
        ...devices["iPhone 13"],
        /*
          기기 서술자는 iPhone 13 것을 쓰되(390×844, 터치, 모바일 UA) 엔진은 크로미움으로
          고정한다. iPhone 13 의 기본 엔진은 webkit 인데, webkit 은 리눅스에서 별도 시스템
          라이브러리를 요구해(`npx playwright install --with-deps webkit`, sudo 필요) 개발
          기기마다 되고 안 되고가 갈린다. 여기서 잡으려는 것은 렌더링 엔진 차이가 아니라
          **폭과 입력 방식**이라 크로미움으로도 충분하다 — hover 없는 환경(pointer: coarse)이
          그대로 재현된다.

          다만 blob iframe 안의 PDF 처럼 엔진이 실제로 갈리는 것은 여기서 못 잡는다.
          그래서 PDF 는 "렌더된다"가 아니라 "폴백 동선이 있다"만 검사한다.
        */
        browserName: "chromium",
        storageState: "e2e/.auth/admin.json",
      },
    },
  ],
});
