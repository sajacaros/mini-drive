// spec: 할 일 체크는 한 컨트롤로 빈칸 → 완료(v) → 건너뜀(x) → 빈칸 을 돈다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("오늘 할 일 상태", () => {
  test("체크를 누를 때마다 빈칸 → 완료 → 건너뜀 → 빈칸 으로 돈다", async ({ page }) => {
    const title = `e2e-todo-cycle-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto("/todo");

      await page.getByPlaceholder("할 일을 추가하세요").fill(title);
      await page.getByRole("button", { name: "추가" }).click();

      const row = page.locator("li", { hasText: title });
      await expect(row).toBeVisible();
      const check = row.getByRole("button", { name: /^상태:/ });

      // 새 항목은 빈칸(미완료)에서 시작한다.
      await expect(check).toHaveAttribute("aria-label", "상태: 미완료 (누르면 완료)");

      // 1번째 클릭 → 완료. 달성률 분모/분자에 함께 잡힌다.
      await check.click();
      await expect(check).toHaveAttribute("aria-label", "상태: 완료 (누르면 건너뜀)");

      // 2번째 클릭 → 건너뜀. 달성률 분모(actionable)에서 빠지고 "건너뜀" 카운트로 옮겨간다.
      await check.click();
      await expect(check).toHaveAttribute("aria-label", "상태: 건너뜀 (누르면 미완료)");
      await expect(page.getByText(/건너뜀/)).toBeVisible();

      // 3번째 클릭 → 다시 빈칸.
      await check.click();
      await expect(check).toHaveAttribute("aria-label", "상태: 미완료 (누르면 완료)");

      // 서버에 저장된 상태다 — 새로고침해도 유지된다.
      await check.click();
      await expect(check).toHaveAttribute("aria-label", "상태: 완료 (누르면 건너뜀)");
      await page.reload();
      await expect(
        page.locator("li", { hasText: title }).getByRole("button", { name: /^상태:/ }),
      ).toHaveAttribute("aria-label", "상태: 완료 (누르면 건너뜀)");
    } finally {
      await page.goto("/todo");
      const row = page.locator("li", { hasText: title });
      if (await existsNow(row)) {
        await row.getByRole("button", { name: "삭제" }).click();
        await expect(row).toHaveCount(0);
      }
    }
  });
});
