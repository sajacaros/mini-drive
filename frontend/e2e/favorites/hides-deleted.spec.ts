// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("즐겨찾기", () => {
  test("[엣지] 삭제된 파일은 즐겨찾기 뷰에서 숨겨진다", async ({ page }) => {
    const fileName = `e2e-fav-hides-deleted-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e favorites hides-deleted test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드하고 즐겨찾기에 등록
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      await row.getByRole("button", { name: "즐겨찾기", exact: true }).click();
      await expect(row.getByRole("button", { name: "즐겨찾기 해제" })).toBeVisible();

      await page.goto("/favorites");
      // expect: /favorites 에 파일이 보인다
      await expect(page.getByRole("row", { name: namePattern })).toBeVisible();

      // 2. 드라이브 홈에서 해당 파일을 삭제(휴지통으로 이동)
      await page.goto("/");
      const homeRow = page.getByRole("row", { name: namePattern });
      await homeRow.getByLabel("삭제").click();
      await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
      await page.getByRole("button", { name: "휴지통으로 이동" }).click();

      // expect: 삭제 토스트가 보인다
      await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();

      // 3. /favorites 로 이동
      await page.goto("/favorites");

      // expect: 삭제된 파일이 더 이상 즐겨찾기 목록에 나타나지 않는다
      await expect(page.getByRole("row", { name: namePattern })).not.toBeVisible();
      // 유일한 즐겨찾기였다면 빈 상태 문구가 보인다
      await expect(page.getByText("즐겨찾기한 항목이 없습니다")).toBeVisible();
    } finally {
      // 4. /trash 에서 파일을 '영구 삭제'로 완전히 제거
      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: namePattern });
      if (await existsNow(trashRow)) {
        await trashRow.getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
        await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
        await expect(trashRow).not.toBeVisible();
      }

      // 드라이브 홈에 아직 남아있을 경우(중간 단계 실패 대비) 최종 정리
      await page.goto("/");
      const row = page.getByRole("row", { name: namePattern });
      if (await existsNow(row)) {
        await row.getByLabel("삭제").click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        await expect(row).not.toBeVisible();

        await page.goto("/trash");
        const leftoverTrashRow = page.getByRole("row", { name: namePattern });
        if (await existsNow(leftoverTrashRow)) {
          await leftoverTrashRow.getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
          await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
          await expect(leftoverTrashRow).not.toBeVisible();
        }
      }

      fs.rmSync(filePath, { force: true });
    }
  });
});
