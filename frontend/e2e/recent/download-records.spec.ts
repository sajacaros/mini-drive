// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("최근 이용 콘텐츠", () => {
  test("파일 다운로드 후 최근 뷰에 반영된다", async ({ page }) => {
    const fileName = `e2e-recent-download-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e recent download-records test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드한다(사전에 미리보기는 하지 않음)
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });

      // expect: /recent 에 해당 파일이 없다(다운로드 전이므로)
      await page.goto("/recent");
      await expect(page.getByRole("row", { name: namePattern })).not.toBeVisible();

      // 2. 파일 행의 '다운로드' 버튼 클릭(Playwright download 이벤트 대기)
      await page.goto("/");
      const downloadPromise = page.waitForEvent("download");
      await row.getByRole("button", { name: "다운로드" }).click();
      // expect: 다운로드가 트리거된다
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toBe(fileName);

      // 3. /recent 로 이동
      await page.goto("/recent");

      // expect: 해당 파일이 최근 목록에 나타난다(미리보기 없이도 다운로드만으로 기록됨)
      const recentRow = page.getByRole("row", { name: namePattern });
      await expect(recentRow).toBeVisible();
      await expect(recentRow.getByRole("button", { name: "다운로드" })).toBeVisible();
    } finally {
      // 4. 테스트 정리: 파일 삭제 및 영구 삭제
      await page.goto("/");
      const row = page.getByRole("row", { name: namePattern });
      if (await existsNow(row)) {
        await row.getByLabel("삭제").click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        await expect(row).not.toBeVisible();
      }

      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: namePattern });
      if (await existsNow(trashRow)) {
        await trashRow.getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
        await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
        // expect: 드라이브가 정리된다
        await expect(trashRow).not.toBeVisible();
      }

      fs.rmSync(filePath, { force: true });
    }
  });
});
