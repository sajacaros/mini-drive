// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("최근 이용 콘텐츠", () => {
  test("[엣지] 폴더 열람은 최근 기록에 포함되지 않는다", async ({ page }) => {
    const folderName = `e2e-recent-folder-${Date.now()}`;
    const namePattern = new RegExp(folderName);

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. '새 폴더'로 테스트 폴더를 생성한 뒤 폴더명을 클릭해 하위 폴더로 진입(열람)한다
      await page.getByRole("button", { name: "새 폴더" }).click();
      await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
      await page.getByRole("textbox", { name: "폴더 이름" }).fill(folderName);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();

      const folderRow = page.getByRole("row", { name: namePattern });
      await expect(folderRow).toBeVisible();
      await folderRow.getByRole("button", { name: folderName }).click();

      // expect: 폴더 내부(빈 상태)로 이동한다
      await expect(page.getByText("이 폴더가 비어 있습니다")).toBeVisible();

      // 2. 드라이브 홈으로 돌아와 확인
      await page.goto("/");

      // expect: "최근 항목" 스트립에 방금 연 폴더가 포함되지 않는다
      await expect(page.getByRole("heading", { name: "최근 항목" })).not.toBeVisible();

      // 3. /recent 로 이동
      await page.goto("/recent");

      // expect: 폴더가 최근 목록에 나타나지 않는다(빈 상태라면 빈 상태 문구가 보인다)
      await expect(page.getByRole("row", { name: namePattern })).not.toBeVisible();
      await expect(page.getByText("최근 이용한 항목이 없습니다")).toBeVisible();
    } finally {
      // 4. 테스트 정리: 폴더 삭제 및 영구 삭제
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
    }
  });
});
