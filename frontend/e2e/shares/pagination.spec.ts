// spec: 공유 링크 목록은 20건 단위로 페이지네이션되고, 탭을 바꾸면 1페이지로 돌아간다.
// seed: frontend/e2e/support/shares.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { purgeSeededFile, seedShares } from "../support/shares";

const PAGE_SIZE = 20;

test.describe("공유 링크 페이지네이션", () => {
  test("20건을 넘기면 페이지가 나뉘고, 탭 전환 시 1페이지로 돌아간다", async ({ page }) => {
    const fileName = `e2e-share-page-${Date.now()}.txt`;
    let fileId = 0;

    try {
      await loginAsAdmin(page);
      // 한 페이지(20건)를 넘기도록 21건을 시드한다.
      fileId = await seedShares(page, fileName, PAGE_SIZE + 1);

      // 사이드바에는 관리자용 "/admin/shares" 링크도 같은 이름으로 있어 경로로 직접 이동한다.
      await page.goto("/shares");

      // 1페이지는 정확히 20행이고, 이전은 막혀 있다.
      const rows = page.locator("table tbody tr");
      await expect(rows).toHaveCount(PAGE_SIZE);
      const prev = page.getByRole("button", { name: "이전" });
      const next = page.getByRole("button", { name: "다음" });
      await expect(page.getByText(/^1 \/ [2-9]\d*$/)).toBeVisible();
      await expect(prev).toBeDisabled();
      await expect(next).toBeEnabled();

      // 다음 페이지로 이동 — 이전이 열리고 나머지 행이 보인다.
      await next.click();
      await expect(page.getByText(/^2 \/ [2-9]\d*$/)).toBeVisible();
      await expect(prev).toBeEnabled();
      await expect(rows.first()).toBeVisible();

      // 탭을 바꾸면 페이지가 1로 초기화된다(2페이지에 머물러 빈 화면이 뜨지 않도록).
      await page.getByRole("button", { name: "비활성", exact: true }).click();
      await page.getByRole("button", { name: "활성", exact: true }).click();
      await expect(page.getByText(/^1 \/ [2-9]\d*$/)).toBeVisible();
      await expect(page.getByRole("button", { name: "이전" })).toBeDisabled();
    } finally {
      if (fileId) await purgeSeededFile(page, fileId);
    }
  });
});
