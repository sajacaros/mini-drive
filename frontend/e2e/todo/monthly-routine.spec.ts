// spec: spec/minidrive-prd.md 3.8.1 (반복 루틴 monthly — 매월 특정일)
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/** 서비스 기준 '오늘'은 KST 고정이라(backend/app/services/todos.py) 같은 시간대로 일자를 뽑는다. */
function todayDayOfMonthKst(): number {
  const kst = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
  return Number(kst.split("-")[2]);
}

test.describe("매월 특정일 루틴", () => {
  test("오늘 일자로 만든 매월 루틴은 오늘 할 일에 채워진다", async ({ page }) => {
    const title = `e2e-monthly-${Date.now()}`;
    const day = todayDayOfMonthKst();

    await loginAsAdmin(page);
    await page.goto("/routines");

    try {
      // 1. '매월 특정일' 주기로 오늘 일자를 골라 루틴을 만든다.
      await page.getByRole("button", { name: "루틴 추가" }).click();
      await page.getByPlaceholder("예: 매일 운동 30분").fill(title);
      await page.getByRole("button", { name: "매월 특정일" }).click();
      await page.getByLabel("날짜 선택").selectOption(String(day));
      await page.getByRole("button", { name: "저장" }).click();
      await expect(page.getByText("루틴을 만들었습니다.")).toBeVisible();

      // expect: 목록에 "매월 N일" 배지가 붙는다
      const card = page.locator("li", { hasText: title });
      await expect(card).toContainText(`매월 ${day}일`);

      // 2. 오늘 할 일로 이동 — 조회 시점에 물질화된다(크론 없음).
      await page.goto("/todo");

      // expect: 오늘 일자와 맞으므로 루틴 항목이 나타난다
      await expect(page.locator("li", { hasText: title })).toBeVisible();
    } finally {
      // 루틴을 지우면 파생 항목은 임시 항목으로 남으므로 양쪽 다 치운다.
      await page.goto("/routines");
      const card = page.locator("li", { hasText: title });
      if (await existsNow(card)) {
        page.once("dialog", (d) => void d.accept());
        await card.getByRole("button", { name: "삭제" }).click();
        await expect(card).toHaveCount(0);
      }
      await page.goto("/todo");
      const item = page.locator("li", { hasText: title });
      if (await existsNow(item)) {
        await item.getByRole("button", { name: "삭제" }).click();
        await expect(item).toHaveCount(0);
      }
    }
  });
});
