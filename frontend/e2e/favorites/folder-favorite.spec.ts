// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("즐겨찾기", () => {
  test("폴더도 즐겨찾기 등록/해제가 가능하다", async ({ page }) => {
    const folderName = `e2e-fav-folder-${Date.now()}`;
    const namePattern = new RegExp(folderName);

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      // 1. '새 폴더'로 테스트 폴더를 생성
      await page.getByRole("button", { name: "새 폴더" }).click();
      await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
      await page.getByRole("textbox", { name: "폴더 이름" }).fill(folderName);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();

      // expect: 목록에 폴더 행이 나타난다
      const folderRow = page.getByRole("row", { name: namePattern });
      await expect(folderRow).toBeVisible();

      // 2. 폴더 행의 별 아이콘(aria-label="즐겨찾기") 클릭
      const starButton = folderRow.getByRole("button", { name: "즐겨찾기", exact: true });
      await starButton.click();

      // expect: aria-label이 "즐겨찾기 해제"로 바뀐다
      const unfavoriteButton = folderRow.getByRole("button", { name: "즐겨찾기 해제" });
      await expect(unfavoriteButton).toBeVisible();
      await expect(unfavoriteButton).toHaveAttribute("aria-pressed", "true");

      // 3. /favorites 로 이동
      await page.goto("/favorites");

      // expect: 폴더가 즐겨찾기 목록에 나타난다(폴더 아이콘과 함께)
      // 아이콘은 장식용 SVG(aria-hidden)라 role=img로 노출되지 않으므로 svg 요소로 직접 확인한다
      const favoritesRow = page.getByRole("row", { name: namePattern });
      await expect(favoritesRow).toBeVisible();
      await expect(favoritesRow.locator("svg").first()).toBeVisible();
      await expect(
        favoritesRow.getByRole("button", { name: "즐겨찾기 해제" }),
      ).toHaveAttribute("aria-pressed", "true");
    } finally {
      // 4. 폴더를 삭제(휴지통 이동) 후 휴지통에서 영구 삭제하여 정리
      await page.goto("/");
      const folderRow = page.getByRole("row", { name: namePattern });
      if (await existsNow(folderRow)) {
        await folderRow.getByLabel("삭제").click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        await expect(folderRow).not.toBeVisible();
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
    }
  });
});
