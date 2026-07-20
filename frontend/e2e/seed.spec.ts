import { test, expect } from "@playwright/test";

/**
 * 환경 seed — 인증 헬퍼는 부작용 없는 `support/auth.ts` 로 이동했고 여기서 재수출한다.
 * 개별 스펙은 계속 `import { loginAsAdmin } from "../support/auth"` 로 참조한다.
 * 실제 로그인은 전역 `auth.setup.ts` 가 한 번만 수행하고 storageState 로 재사용된다
 * (매 테스트 재로그인 시 로그인 rate limit — IP 당 5회/분 — 에 걸리던 문제 해결).
 */
export { ADMIN_EMAIL, ADMIN_PASSWORD, loginAsAdmin } from "./support/auth";

import { loginAsAdmin } from "./support/auth";

test.describe("seed", () => {
  test("인증 상태에서 드라이브와 Phase 8 사이드바 항목이 보인다", async ({ page }) => {
    await loginAsAdmin(page);
    // Phase 8 사이드바 항목이 노출되는지 확인(즐겨찾기·최근).
    await expect(page.getByRole("link", { name: "즐겨찾기" })).toBeVisible();
    await expect(page.getByRole("link", { name: "최근" })).toBeVisible();
  });
});
