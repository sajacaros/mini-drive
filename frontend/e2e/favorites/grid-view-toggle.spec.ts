// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("즐겨찾기", () => {
  test("그리드 보기에서도 즐겨찾기 토글과 표시가 동일하게 동작한다", async ({ page }) => {
    const fileName = `e2e-fav-grid-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e favorites grid-view-toggle test content ${Date.now()}`);

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드한 뒤 툴바에서 '그리드 보기' 버튼을 클릭
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);
      await expect(page.getByText(fileName)).toBeVisible();

      await page.getByRole("button", { name: "그리드 보기" }).click();

      // expect: 카드형 레이아웃으로 전환된다(그리드 보기 버튼이 pressed 상태)
      await expect(page.getByRole("button", { name: "그리드 보기" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );

      // 2. 파일 카드의 별 아이콘 클릭
      // 그리드 카드는 "group card" 클래스를 함께 쓴다(업로드 진행률 카드는 "card"만 있어 구분 필요)
      const card = page.locator("div.group.card").filter({ hasText: fileName });
      await expect(card).toBeVisible();
      const starButton = card.getByRole("button", { name: "즐겨찾기", exact: true });
      await starButton.click();

      // expect: 즐겨찾기 등록 상태(pressed)가 된다
      const unfavoriteButton = card.getByRole("button", { name: "즐겨찾기 해제" });
      await expect(unfavoriteButton).toBeVisible();
      await expect(unfavoriteButton).toHaveAttribute("aria-pressed", "true");

      // 3. /favorites 로 이동 후 '그리드 보기'로 전환
      await page.goto("/favorites");
      await page.getByRole("button", { name: "그리드 보기" }).click();
      await expect(page.getByRole("button", { name: "그리드 보기" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );

      // expect: 파일 카드가 보이고, 미리보기·다운로드 액션 버튼과 pressed 별 아이콘이 함께 노출된다
      const favoritesCard = page.locator("div.group.card").filter({ hasText: fileName });
      await expect(favoritesCard).toBeVisible();
      await expect(favoritesCard.getByRole("button", { name: "미리보기" })).toBeVisible();
      await expect(favoritesCard.getByRole("button", { name: "다운로드" })).toBeVisible();
      await expect(
        favoritesCard.getByRole("button", { name: "즐겨찾기 해제" }),
      ).toHaveAttribute("aria-pressed", "true");
    } finally {
      // 4. 테스트 정리: 즐겨찾기 해제, 파일 삭제 및 영구 삭제
      await page.goto("/");
      // 그리드 카드는 "group card" 클래스를 함께 쓴다(업로드 진행률 카드는 "card"만 있어 구분 필요)
      const card = page.locator("div.group.card").filter({ hasText: fileName });
      if (await existsNow(card)) {
        const unfavoriteButton = card.getByRole("button", { name: "즐겨찾기 해제" });
        if (await existsNow(unfavoriteButton)) {
          await unfavoriteButton.click();
        }
        await card.getByRole("button", { name: "삭제" }).click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        await expect(card).not.toBeVisible();
      }

      // 그리드 보기 상태가 다른 테스트에 영향을 주지 않도록 목록 보기로 복원
      await page.goto("/");
      const listViewButton = page.getByRole("button", { name: "목록 보기" });
      if ((await listViewButton.getAttribute("aria-pressed")) !== "true") {
        await listViewButton.click();
      }

      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: new RegExp(fileName.replace(".", "\\.")) });
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
