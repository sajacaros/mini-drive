// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow, recentStrip } from "../support/locators";

test.describe("최근 이용 콘텐츠", () => {
  test("파일 미리보기 후 최근 뷰와 홈 스트립에 반영된다", async ({ page }) => {
    const fileName = `e2e-recent-preview-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e recent preview-records test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드한 뒤 해당 행의 '미리보기' 버튼 클릭
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      await row.getByRole("button", { name: "미리보기" }).click();

      // expect: 미리보기 다이얼로그(heading = 파일명)에 파일 내용이 표시된다
      await expect(page.getByRole("heading", { name: fileName })).toBeVisible();
      await expect(page.getByRole("dialog")).toContainText("e2e recent preview-records test content");

      // 2. 다이얼로그를 '닫기' 버튼으로 닫고 드라이브 홈을 확인
      await page.getByRole("button", { name: "닫기" }).click();
      // 최근 항목 기록은 드라이브 홈 재방문 시 반영된다
      await page.goto("/");

      // expect: heading "최근 항목" 스트립이 나타나고 해당 파일명 카드가 포함된다
      // 스트립의 카드 버튼과 파일 목록 행의 버튼이 접근성 이름이 같으므로 스트립으로 범위를 좁힌다
      await expect(page.getByRole("heading", { name: "최근 항목" })).toBeVisible();
      await expect(recentStrip(page).getByRole("button", { name: fileName })).toBeVisible();

      // 3. 사이드바 '최근'(/recent)으로 이동
      await page.getByRole("link", { name: "최근" }).click();
      await expect(page).toHaveURL(/\/recent$/);

      // expect: 같은 파일이 최근 목록 테이블에 나타나며 행 액션으로 "다운로드" 버튼이 노출된다
      const recentRow = page.getByRole("row", { name: namePattern });
      await expect(recentRow).toBeVisible();
      await expect(recentRow.getByRole("button", { name: "다운로드" })).toBeVisible();
      await expect(recentRow.getByRole("button", { name: "미리보기" })).not.toBeVisible();
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

      // expect: 최근 항목 스트립/뷰에서 함께 사라지고 드라이브가 정리된다
      await page.goto("/");
      await expect(page.getByRole("button", { name: fileName })).not.toBeVisible();

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
