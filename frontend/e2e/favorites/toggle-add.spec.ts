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

      /*
        2~3. 숨김/노출은 **계산된 opacity** 로 확인한다. 예전에는 opacity-0 /
        group-hover:opacity-100 이라는 클래스 이름을 직접 봤는데, 그러면 구현을 바꾸는 순간
        동작이 멀쩡해도 깨진다. 실제로 이 숨김은 이제 `.row-actions` 가 미디어 쿼리
        (hover: hover)로 처리한다 — 손가락 기기에서는 아예 숨기지 않는다.
        이 프로젝트는 데스크톱 크로미움이라 hover 가 있으므로 숨김이 적용된다.
      */
      const starOpacity = () =>
        starButton.evaluate((el) => getComputedStyle(el).opacity);

      // expect: hover 전에는 보이지 않는다
      await expect.poll(starOpacity).toBe("0");

      // 3. 해당 행에 마우스를 hover 한다
      await row.hover();
      // expect: 별 아이콘이 시각적으로 노출된다
      await expect.poll(starOpacity).toBe("1");

      // 4. 별 아이콘(aria-label="즐겨찾기") 클릭
      await starButton.click();

      // expect: 버튼의 aria-label이 "즐겨찾기 해제"로 바뀌고 pressed 상태가 된다
      const unfavoriteButton = row.getByRole("button", { name: "즐겨찾기 해제" });
      await expect(unfavoriteButton).toBeVisible();
      await expect(unfavoriteButton).toHaveAttribute("aria-pressed", "true");

      // expect: hover를 해제해도(마우스를 다른 곳으로 이동) 별 아이콘이 계속 보인다
      // — 즐겨찾기가 켜진 별은 hover 숨김 대상에서 빠진다.
      await page.getByRole("link", { name: "내 드라이브" }).hover();
      await expect(unfavoriteButton).toBeVisible();
      await expect
        .poll(() => unfavoriteButton.evaluate((el) => getComputedStyle(el).opacity))
        .toBe("1");

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
