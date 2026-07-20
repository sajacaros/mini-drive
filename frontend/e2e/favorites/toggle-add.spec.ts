// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("즐겨찾기", () => {
  test("행 hover 시 별 아이콘이 노출되고 클릭으로 즐겨찾기 등록 → 즐겨찾기 뷰에 반영", async ({
    page,
  }) => {
    const fileName = `e2e-fav-toggle-add-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e favorites toggle-add test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 드라이브 홈에서 테스트 파일을 업로드한다
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      // expect: 목록에 파일 행이 나타난다
      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      const starButton = row.getByRole("button", { name: "즐겨찾기", exact: true });

      // 2. hover 하지 않은 상태에서 별 아이콘 버튼(aria-label="즐겨찾기")의 opacity를 확인
      // expect: hover 전에는 opacity가 0(시각적으로 숨김) 이다
      await expect(starButton).toHaveClass(/opacity-0/);

      // 3. 해당 행에 마우스를 hover 한다
      await row.hover();
      // expect: 별 아이콘이 시각적으로 노출된다(group-hover 클래스 적용, opacity-0 은 유지되나
      // group-hover:opacity-100 로 실제 노출됨)
      await expect(starButton).toHaveClass(/group-hover:opacity-100/);

      // 4. 별 아이콘(aria-label="즐겨찾기") 클릭
      await starButton.click();

      // expect: 버튼의 aria-label이 "즐겨찾기 해제"로 바뀌고 pressed 상태가 된다
      const unfavoriteButton = row.getByRole("button", { name: "즐겨찾기 해제" });
      await expect(unfavoriteButton).toBeVisible();
      await expect(unfavoriteButton).toHaveAttribute("aria-pressed", "true");

      // expect: hover를 해제해도(마우스를 다른 곳으로 이동) 별 아이콘이 계속 보인다
      // (opacity-0 클래스가 제거됨)
      await page.getByRole("link", { name: "내 드라이브" }).hover();
      await expect(unfavoriteButton).toBeVisible();
      await expect(unfavoriteButton).not.toHaveClass(/opacity-0/);

      // 5. 사이드바 '즐겨찾기'(/favorites)로 이동
      await page.getByRole("link", { name: "즐겨찾기" }).click();
      await expect(page).toHaveURL(/\/favorites$/);

      // expect: 해당 파일이 목록에 나타나고 별 아이콘은 pressed("즐겨찾기 해제") 상태로 항상 표시된다
      const favoritesRow = page.getByRole("row", { name: namePattern });
      await expect(favoritesRow).toBeVisible();
      const favoritesUnfavoriteButton = favoritesRow.getByRole("button", {
        name: "즐겨찾기 해제",
      });
      await expect(favoritesUnfavoriteButton).toBeVisible();
      await expect(favoritesUnfavoriteButton).toHaveAttribute("aria-pressed", "true");
    } finally {
      // 6. 테스트 정리: 파일을 삭제(휴지통 이동) 후 영구 삭제
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
