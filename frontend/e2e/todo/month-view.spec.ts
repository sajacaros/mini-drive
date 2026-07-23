// spec: 할 일 월간 화면 — 달력 격자에 날짜별 (완료/전체) 개수가 나오고, 날짜를 누르면 일간으로 간다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("할 일 월간 보기", () => {
  test("달력에 오늘 개수가 나오고 날짜 클릭으로 일간 화면에 간다", async ({ page }) => {
    const title = `e2e-todo-month-${Date.now()}`;
    // 프론트 날짜 계산은 브라우저 로컬 기준(lib/localDate.ts)이고, 테스트 러너와 브라우저는
    // 같은 시스템 시간대를 쓰므로 여기서도 로컬 Date 로 오늘을 뽑는다.
    const now = new Date();
    const month = now.getMonth() + 1;
    const day = now.getDate();

    try {
      await loginAsAdmin(page);
      await page.goto("/todo");

      await page.getByPlaceholder("할 일을 추가하세요").fill(title);
      await page.getByRole("button", { name: "추가" }).click();
      await expect(page.locator("li", { hasText: title })).toBeVisible();

      const text =
        (await page.getByText(/\d+ \/ \d+ 완료/).first().textContent()) ?? "";
      const m = text.match(/(\d+) \/ (\d+) 완료/);
      const done = Number(m?.[1]);
      const total = Number(m?.[2]);

      // 월간 화면으로 이동 — 이번 달 라벨이 보인다.
      await page.getByRole("button", { name: "월간" }).click();
      await expect(page.getByText(`${now.getFullYear()}년 ${month}월`)).toBeVisible();

      // 오늘 칸에 일간 진행률과 같은 (완료/전체) 개수가 나온다.
      const todayCell = page.getByRole("button", {
        name: new RegExp(`^${month}월 ${day}일`),
      });
      await expect(todayCell).toContainText(`${done}/${total}`);

      // 날짜를 누르면 그날의 일간 화면으로 간다.
      await todayCell.click();
      await expect(page).toHaveURL(/\/todo\?date=/);
      await expect(page.locator("li", { hasText: title })).toBeVisible();
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
