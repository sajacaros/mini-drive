// spec: 공유 링크 목록은 활성/비활성 탭으로 나뉜다.
// seed: frontend/e2e/support/shares.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { purgeSeededFile, seedShares } from "../support/shares";

test.describe("공유 링크 탭", () => {
  test("비활성화하면 활성 탭에서 사라지고 비활성 탭으로 옮겨간다", async ({ page }) => {
    const fileName = `e2e-share-tab-${Date.now()}.txt`;
    const rowPattern = new RegExp(fileName.replace(".", "\\."));
    let fileId = 0;

    try {
      await loginAsAdmin(page);
      fileId = await seedShares(page, fileName, 1);

      // 사이드바에는 관리자용 "/admin/shares" 링크도 같은 이름으로 있어 경로로 직접 이동한다.
      await page.goto("/shares");

      // 기본은 활성 탭 — 방금 만든 링크가 최신순 맨 위에 보인다.
      const row = page.getByRole("row", { name: rowPattern });
      await expect(row).toBeVisible();

      // 비활성화 → 활성 탭에서 즉시 사라진다.
      await row.getByRole("button", { name: "비활성화" }).click();
      await expect(page.getByText("공유를 비활성화했습니다.")).toBeVisible();
      await expect(page.getByRole("row", { name: rowPattern })).toHaveCount(0);

      // 비활성 탭으로 옮겨가 이력이 남는다(행 삭제가 아니라 is_active=FALSE).
      await page.getByRole("button", { name: "비활성", exact: true }).click();
      await expect(page.getByRole("row", { name: rowPattern })).toBeVisible();
      // 비활성 항목에는 비활성화 버튼이 없다.
      await expect(
        page.getByRole("row", { name: rowPattern }).getByRole("button", { name: "비활성화" }),
      ).toHaveCount(0);

      // 활성 탭으로 되돌리면 다시 보이지 않는다.
      await page.getByRole("button", { name: "활성", exact: true }).click();
      await expect(page.getByRole("row", { name: rowPattern })).toHaveCount(0);
    } finally {
      if (fileId) await purgeSeededFile(page, fileId);
    }
  });
});
