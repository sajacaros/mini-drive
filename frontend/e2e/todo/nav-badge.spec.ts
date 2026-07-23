// spec: 왼쪽 네비 '오늘 할 일'에 오늘의 (완료/전체) 배지가 붙고, 체크와 함께 즉시 갱신된다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("네비 오늘 할 일 배지", () => {
  test("(완료/전체) 배지가 진행률과 일치하고 체크하면 분자가 오른다", async ({ page }) => {
    const title = `e2e-nav-badge-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto("/todo");

      await page.getByPlaceholder("할 일을 추가하세요").fill(title);
      await page.getByRole("button", { name: "추가" }).click();
      const row = page.locator("li", { hasText: title });
      await expect(row).toBeVisible();

      // 진행률 카드의 done/total 을 읽어 배지와 대조한다.
      const counts = async () => {
        const text =
          (await page.getByText(/\d+ \/ \d+ 완료/).first().textContent()) ?? "";
        const m = text.match(/(\d+) \/ (\d+) 완료/);
        return { done: Number(m?.[1]), total: Number(m?.[2]) };
      };
      const { done, total } = await counts();

      const navLink = page.getByRole("link", { name: "오늘 할 일" });
      await expect(navLink).toContainText(`(${done}/${total})`);

      // 새 항목을 완료로 체크하면 배지 분자가 1 오른다(페이지 새로고침 없이).
      await row.getByRole("button", { name: /^상태:/ }).click();
      await expect(navLink).toContainText(`(${done + 1}/${total})`);
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
