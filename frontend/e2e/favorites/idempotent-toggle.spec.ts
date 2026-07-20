// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("즐겨찾기", () => {
  test("[엣지] 별 아이콘 연속 클릭(더블 토글)에도 최종 상태가 일관적이다 (멱등성)", async ({
    page,
  }) => {
    const fileName = `e2e-fav-idempotent-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e favorites idempotent-toggle test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드한다
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      // expect: 파일 행이 보인다(미등록 상태, aria-label="즐겨찾기")
      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      const starButton = row.getByRole("button", { name: /즐겨찾기/ });
      await expect(starButton).toHaveAttribute("aria-label", "즐겨찾기");

      // 2. 별 아이콘을 빠르게 두 번 연속 클릭한다(등록→해제)
      await starButton.click();
      await starButton.click();

      // expect: 에러 토스트 없이 최종 상태가 "즐겨찾기"(미등록)로 안정적으로 수렴한다
      await expect(starButton).toHaveAttribute("aria-label", "즐겨찾기");
      await expect(page.getByText("오류")).not.toBeVisible();

      // expect: /favorites 로 이동 시 해당 파일이 목록에 없다(빈 상태이거나 다른 항목만 존재)
      await page.goto("/favorites");
      await expect(page.getByRole("row", { name: namePattern })).not.toBeVisible();

      // 3. 별 아이콘을 다시 한 번 클릭해 등록 상태로 만든 뒤 페이지를 새로고침한다
      await page.goto("/");
      const homeRow = page.getByRole("row", { name: namePattern });
      const homeStarButton = homeRow.getByRole("button", { name: /즐겨찾기/ });
      await homeStarButton.click();
      await expect(homeStarButton).toHaveAttribute("aria-label", "즐겨찾기 해제");

      await page.goto("/");

      // expect: 새로고침 후에도 별 아이콘이 pressed("즐겨찾기 해제") 상태를 유지한다
      const reloadedButton = page
        .getByRole("row", { name: namePattern })
        .getByRole("button", { name: "즐겨찾기 해제" });
      await expect(reloadedButton).toBeVisible();
      await expect(reloadedButton).toHaveAttribute("aria-pressed", "true");
    } finally {
      // 4. 테스트 정리: 즐겨찾기 해제 후 파일 삭제 및 영구 삭제
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
