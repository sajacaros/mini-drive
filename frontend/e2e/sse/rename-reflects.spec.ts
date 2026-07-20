// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow, recentStrip } from "../support/locators";

test.describe("실시간 목록 갱신 (SSE)", () => {
  test("이름 변경이 목록과 최근 항목 스트립에 즉시 반영된다", async ({ page }) => {
    const fileName = `e2e-sse-rename-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e rename reflects test content ${Date.now()}`);
    const newFileName = `e2e-sse-renamed-${Date.now()}.txt`;
    const namePattern = new RegExp(fileName.replace(".", "\\."));
    const newNamePattern = new RegExp(newFileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드하고 '미리보기'를 1회 실행해 최근 항목에 등록되게 한 뒤 드라이브 홈으로 복귀
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      await row.getByRole("button", { name: "미리보기" }).click();
      await expect(page.getByRole("heading", { name: fileName })).toBeVisible();
      await page.getByRole("button", { name: "닫기" }).click();

      // 최근 항목 기록은 드라이브 홈 재방문 시 반영된다
      await page.goto("/");

      // expect: 드라이브 홈 상단에 heading "최근 항목" 스트립과 해당 파일명 카드가 보인다
      // 스트립의 카드 버튼과 파일 목록 행의 버튼이 접근성 이름이 같으므로 스트립으로 범위를 좁힌다
      await expect(page.getByRole("heading", { name: "최근 항목" })).toBeVisible();
      await expect(recentStrip(page).getByRole("button", { name: fileName })).toBeVisible();

      // 2. 해당 파일 행의 '이름 변경' 버튼 클릭 → textbox 값을 새 이름으로 변경 → '변경' 클릭
      await row.getByRole("button", { name: "이름 변경" }).click();
      await expect(page.getByRole("heading", { name: "이름 변경" })).toBeVisible();
      const renameTextbox = page.getByRole("dialog").getByRole("textbox");
      await expect(renameTextbox).toHaveValue(fileName);
      await renameTextbox.fill(newFileName);
      await page.getByRole("button", { name: "변경", exact: true }).click();

      // expect: 토스트 "이름을 변경했습니다."가 보인다
      await expect(page.getByText("이름을 변경했습니다.")).toBeVisible();
      // expect: 목록 행의 파일명이 즉시 새 이름으로 바뀐다(재조회 없이)
      const renamedRow = page.getByRole("row", { name: newNamePattern });
      await expect(renamedRow).toBeVisible();
      await expect(row).not.toBeVisible();
      // expect: "최근 항목" 스트립의 카드 라벨도 같은 새 이름으로 동시에 갱신된다
      await expect(recentStrip(page).getByRole("button", { name: newFileName })).toBeVisible();

      // 3. 파일을 삭제(휴지통 이동) 후 휴지통에서 영구 삭제하여 정리
      await renamedRow.getByRole("button", { name: "삭제" }).click();
      await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
      await page.getByRole("button", { name: "휴지통으로 이동" }).click();
      await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
      await expect(renamedRow).not.toBeVisible();

      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: newNamePattern });
      await trashRow.getByRole("button", { name: "영구 삭제" }).click();
      await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
      await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
      await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
      // expect: 드라이브가 정리된다
      await expect(trashRow).not.toBeVisible();
    } finally {
      // 중간 단언이 실패해 happy-path 정리를 건너뛰었더라도 항상 정리한다(best-effort, 멱등).
      // 이름 변경 전/후 두 이름 모두 남아있을 수 있으므로 둘 다 확인한다.
      for (const pattern of [namePattern, newNamePattern]) {
        await page.goto("/");
        const row = page.getByRole("row", { name: pattern });
        if (await existsNow(row)) {
          await row.getByLabel("삭제").click();
          await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
          await page.getByRole("button", { name: "휴지통으로 이동" }).click();
          await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
          await expect(row).not.toBeVisible();
        }

        await page.goto("/trash");
        const trashRow = page.getByRole("row", { name: pattern });
        if (await existsNow(trashRow)) {
          await trashRow.getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
          await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
          await expect(trashRow).not.toBeVisible();
        }
      }

      fs.rmSync(filePath, { force: true });
    }
  });
});
