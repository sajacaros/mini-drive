// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("즐겨찾기", () => {
  test("즐겨찾기 해제 시 즐겨찾기 뷰에서 즉시 사라지고 빈 상태로 복귀한다", async ({ page }) => {
    const fileName = `e2e-fav-toggle-remove-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e favorites toggle-remove test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드하고 즐겨찾기에 등록한 뒤 /favorites 로 이동
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      await row.getByRole("button", { name: "즐겨찾기", exact: true }).click();
      await expect(row.getByRole("button", { name: "즐겨찾기 해제" })).toBeVisible();

      await page.goto("/favorites");

      // expect: 파일이 즐겨찾기 목록에 보인다(유일한 항목)
      const favoritesRow = page.getByRole("row", { name: namePattern });
      await expect(favoritesRow).toBeVisible();
      const unfavoriteButton = favoritesRow.getByRole("button", { name: "즐겨찾기 해제" });
      await expect(unfavoriteButton).toBeVisible();

      // 2. 해당 행의 "즐겨찾기 해제" 버튼 클릭
      await unfavoriteButton.click();

      // expect: 페이지를 새로고침하지 않아도 해당 행이 즉시 목록에서 제거된다
      await expect(favoritesRow).not.toBeVisible();
      // expect: 다른 즐겨찾기 항목이 없으므로 빈 상태 문구가 다시 나타난다
      await expect(page.getByText("즐겨찾기한 항목이 없습니다")).toBeVisible();

      // 3. 드라이브 홈(/)으로 이동해 해당 파일 행을 확인
      await page.goto("/");
      const homeRow = page.getByRole("row", { name: namePattern });

      // expect: 파일은 여전히 드라이브에 존재하며(삭제되지 않음)
      await expect(homeRow).toBeVisible();
      // expect: 별 아이콘은 다시 hover-only 상태(aria-label="즐겨찾기")로 돌아와 있다
      await expect(homeRow.getByRole("button", { name: "즐겨찾기", exact: true })).toBeVisible();
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
        await expect(trashRow).not.toBeVisible();
      }

      fs.rmSync(filePath, { force: true });
    }
  });
});
