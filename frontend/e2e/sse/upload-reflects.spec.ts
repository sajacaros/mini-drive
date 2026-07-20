// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("실시간 목록 갱신 (SSE)", () => {
  test("업로드 후 목록이 재조회 없이 즉시 갱신된다", async ({ page }) => {
    const fileName = `e2e-sse-upload-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e upload reflects test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      // 1. loginAsAdmin으로 로그인 후 드라이브 홈(/)으로 이동
      await loginAsAdmin(page);
      await page.goto("/");
      const emptyState = page.getByText("이 폴더가 비어 있습니다");
      if (await existsNow(emptyState)) {
        await expect(emptyState).toBeVisible();
      }

      // 2. 테스트 전용 소스 파일(예: 이 테스트가 생성한 임시 텍스트 파일)을 '업로드' 버튼으로 선택
      const storageText = page.getByText(/\/ 10\.0 GB/);
      const beforeStorage = await storageText.textContent();

      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      // expect: 완료 토스트가 노출된다. 소형(≤1GB)·대형(resumable) 업로드 모두 공통 핸들러
      // (FileBrowserPage.notifyUploadSuccess)로 동일 문구를 띄운다. 전이성 토스트라 느린 단언
      // (행/용량) 앞에서 먼저 검사한다.
      await expect(page.getByText(`${fileName}: 업로드를 완료했습니다.`)).toBeVisible();
      // expect: 파일 선택 즉시(수동 새로고침·재방문 없이) 목록 테이블에 새 행이 나타난다
      await expect(page.getByRole("button", { name: fileName })).toBeVisible();
      // expect: 사이드바 저장 용량 텍스트(예: "0 B / 10.0 GB")가 증가한다
      await expect(storageText).not.toHaveText(beforeStorage ?? "");

      // 3. 업로드한 파일을 삭제(휴지통 이동) 후 휴지통에서 영구 삭제
      const row = page.getByRole("row", { name: namePattern });
      await row.getByLabel("삭제").click();
      await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
      await expect(page.getByText(`${fileName} 항목을 휴지통으로 이동하시겠습니까?`)).toBeVisible();
      await page.getByRole("button", { name: "휴지통으로 이동" }).click();
      await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
      await expect(page.getByRole("button", { name: fileName })).not.toBeVisible();

      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: namePattern });
      await trashRow.getByRole("button", { name: "영구 삭제" }).click();
      await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
      await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
      await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
      // expect: 드라이브가 테스트 시작 전 상태로 복원된다
      await expect(trashRow).not.toBeVisible();
    } finally {
      // 중간 단언이 실패해 happy-path 정리를 건너뛰었더라도 항상 드라이브를 정리한다(best-effort, 멱등).
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
