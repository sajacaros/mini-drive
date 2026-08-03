// spec: 할 일에 10분 단위 시작 시각을 붙이면 종일 항목 아래로 시각순으로 줄을 선다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("할 일 시작 시각", () => {
  test("종일이 위, 그 아래로 시각순으로 정렬된다", async ({ page }) => {
    const stamp = Date.now();
    const late = `e2e-time-late-${stamp}`; // 14:30
    const early = `e2e-time-early-${stamp}`; // 09:00
    const allDay = `e2e-time-allday-${stamp}`; // 종일
    const titles = [late, early, allDay];

    /** 제목 + 시각(null=종일)로 한 건 추가한다. 시각은 시/분 두 select 로 고른다. */
    const add = async (title: string, time: { hour: string; minute: string } | null) => {
      await page.getByPlaceholder("할 일을 추가하세요").fill(title);
      const hour = page.getByLabel("시작 시각 (시)");
      await hour.selectOption(time ? time.hour : "");
      if (time) await page.getByLabel("시작 시각 (분)").selectOption(time.minute);
      await page.getByRole("button", { name: "추가" }).click();
      await expect(page.locator("li", { hasText: title })).toBeVisible();
    };

    try {
      await loginAsAdmin(page);
      await page.goto("/todo");

      // 일부러 뒤죽박죽 넣는다 — 순서를 정하는 건 입력 순서가 아니라 시각이다.
      await add(late, { hour: "14", minute: "30" });
      await add(early, { hour: "9", minute: "0" });
      await add(allDay, null);

      // 시각은 목록에 그대로 보인다(10분 단위라 자투리 분이 없다).
      await expect(page.locator("li", { hasText: late })).toContainText("14:30");
      await expect(page.locator("li", { hasText: early })).toContainText("09:00");

      /** 세 항목이 화면에 나타난 순서. */
      const order = async () => {
        const rows = await page.locator("li[data-todo-id]").allInnerTexts();
        return titles
          .map((t) => ({ t, at: rows.findIndex((r) => r.includes(t)) }))
          .sort((a, b) => a.at - b.at)
          .map((x) => x.t);
      };
      expect(await order()).toEqual([allDay, early, late]);

      // 서버가 정한 순서다 — 새로고침해도 그대로다.
      await page.reload();
      await expect(page.locator("li", { hasText: allDay })).toBeVisible();
      expect(await order()).toEqual([allDay, early, late]);

      // 시각을 지우면 종일이 되어 시각 있는 항목들 위로 올라간다. (종일 항목끼리의 순서는
      // 드래그로 잡는 sort_order 라 여기서 단정하지 않는다 — 시각 무리보다 위라는 게 요점이다.)
      const lateRow = page.locator("li", { hasText: late });
      await lateRow.getByRole("button", { name: "시작 시각 14:30 변경" }).click();
      await lateRow.getByLabel("시작 시각 (시)").selectOption("");
      await expect(lateRow).not.toContainText("14:30");
      const cleared = await order();
      expect(cleared.indexOf(late)).toBeLessThan(cleared.indexOf(early));
      expect(cleared.indexOf(allDay)).toBeLessThan(cleared.indexOf(early));
    } finally {
      await page.goto("/todo");
      for (const title of titles) {
        const row = page.locator("li", { hasText: title });
        if (await existsNow(row)) {
          await row.getByRole("button", { name: "삭제" }).click();
          await expect(row).toHaveCount(0);
        }
      }
    }
  });
});
