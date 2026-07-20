// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

async function previewAndClose(page: import("@playwright/test").Page, namePattern: RegExp) {
  const row = page.getByRole("row", { name: namePattern });
  await row.getByRole("button", { name: "미리보기" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "닫기" }).click();
}

test.describe("최근 이용 콘텐츠", () => {
  test("최근 목록은 최신 이용 순으로 정렬된다", async ({ page }) => {
    const fileNameA = `e2e-recent-order-a-${Date.now()}.txt`;
    const fileNameB = `e2e-recent-order-b-${Date.now()}.txt`;
    const filePathA = path.join(os.tmpdir(), fileNameA);
    const filePathB = path.join(os.tmpdir(), fileNameB);
    fs.writeFileSync(filePathA, `e2e recent ordering test A content ${Date.now()}`);
    fs.writeFileSync(filePathB, `e2e recent ordering test B content ${Date.now()}`);
    const namePatternA = new RegExp(fileNameA.replace(".", "\\."));
    const namePatternB = new RegExp(fileNameB.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일 A, B를 순서대로 업로드
      let fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      let fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePathA);
      // expect: 둘 다 목록에 보인다
      await expect(page.getByRole("row", { name: namePatternA })).toBeVisible();

      fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePathB);
      await expect(page.getByRole("row", { name: namePatternB })).toBeVisible();

      // 2. 먼저 A를 미리보기하고 닫은 뒤, 이어서 B를 미리보기하고 닫는다
      await previewAndClose(page, namePatternA);
      await previewAndClose(page, namePatternB);

      // 3. /recent 로 이동
      await page.goto("/recent");

      // expect: B가 A보다 위(더 최근)에 나타난다(최신순 정렬)
      const recentRows = page.locator("table tbody tr");
      await expect(recentRows.first()).toContainText(fileNameB);
      await expect(recentRows.nth(1)).toContainText(fileNameA);

      // 4. 다시 A를 미리보기한다
      await page.goto("/");
      await previewAndClose(page, namePatternA);
      await page.goto("/recent");

      // expect: A가 다시 최상단으로 올라온다(재열람 시 순서 갱신)
      await expect(recentRows.first()).toContainText(fileNameA);
      await expect(recentRows.nth(1)).toContainText(fileNameB);
    } finally {
      // 5. 테스트 정리: A, B 모두 삭제 및 영구 삭제
      await page.goto("/");
      for (const namePattern of [namePatternA, namePatternB]) {
        const row = page.getByRole("row", { name: namePattern });
        if (await existsNow(row)) {
          await row.getByLabel("삭제").click();
          await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
          await page.getByRole("button", { name: "휴지통으로 이동" }).click();
          await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
          await expect(row).not.toBeVisible();
        }
      }

      await page.goto("/trash");
      for (const namePattern of [namePatternA, namePatternB]) {
        const trashRow = page.getByRole("row", { name: namePattern });
        if (await existsNow(trashRow)) {
          await trashRow.getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
          await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
          await expect(trashRow).not.toBeVisible();
        }
      }

      fs.rmSync(filePathA, { force: true });
      fs.rmSync(filePathB, { force: true });
    }
  });
});
