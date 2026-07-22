// spec: spec/folder-upload-batch.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 폴더 업로드(배치) 해피패스 — 폴더 선택 input(webkitdirectory) 경로.
 *
 * Playwright 의 setFiles 는 디렉터리 경로를 받으면 webkitdirectory input 에 하위 파일을
 * 통째로 넣어 주고, 브라우저가 webkitRelativePath 를 채운다. 즉 사용자가 폴더를 고르는
 * 흐름을 그대로 재현한다.
 */

/** 업로드한 최상위 폴더를 휴지통 경유로 완전히 지운다(멱등). */
async function purgeFolder(page: Page, folderName: string): Promise<void> {
  const namePattern = new RegExp(folderName);

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
}

test.describe("폴더 업로드 (배치)", () => {
  test("중첩 폴더를 구조 그대로 업로드한다", async ({ page }) => {
    const stamp = Date.now();
    const rootName = `e2e-folder-${stamp}`;
    const srcRoot = path.join(os.tmpdir(), rootName);

    // 트리: 최상위 파일 1 + 1단계 폴더(파일 1) + 2단계 폴더(파일 1) = 파일 3, 폴더 2.
    fs.mkdirSync(path.join(srcRoot, "docs", "nested"), { recursive: true });
    fs.writeFileSync(path.join(srcRoot, "root.txt"), "root level");
    fs.writeFileSync(path.join(srcRoot, "docs", "a.txt"), "docs level");
    fs.writeFileSync(path.join(srcRoot, "docs", "nested", "b.txt"), "nested level");

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. "폴더 업로드" 버튼으로 폴더를 통째로 선택한다.
      const chooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "폴더 업로드" }).click();
      const chooser = await chooserPromise;
      expect(chooser.isMultiple()).toBe(true);
      await chooser.setFiles(srcRoot);

      // 2. 사전 검사 다이얼로그 — 바이트를 보내기 전에 요약이 먼저 뜬다.
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: "폴더 업로드" })).toBeVisible();
      // 파일 3개 · 폴더 3개(최상위 + docs + docs/nested) 로 집계된다.
      await expect(dialog.getByText(/파일 3개 · 폴더 3개/)).toBeVisible();
      // 규칙 위반이 없으므로 "건너뜁니다" 경고는 없어야 한다.
      await expect(dialog.getByText(/건너뜁니다/)).toHaveCount(0);

      // 3. 업로드 실행.
      await dialog.getByRole("button", { name: "3개 업로드" }).click();
      await expect(page.getByText("3개 파일을 업로드했습니다.")).toBeVisible({
        timeout: 30_000,
      });

      // 4. 최상위 폴더가 생겼는지 — SSE 로 즉시 반영된다.
      const rootRow = page.getByRole("row", { name: new RegExp(rootName) });
      await expect(rootRow).toBeVisible();

      // 5. 트리를 따라 내려가며 구조가 보존됐는지 확인한다.
      // 폴더 열기는 더블클릭이다(한 번 클릭은 현재 항목 — lib/rowOpen.ts).
      await page.getByRole("button", { name: rootName }).dblclick();
      await expect(page.getByRole("button", { name: "docs" })).toBeVisible();
      await expect(page.getByRole("button", { name: "root.txt" })).toBeVisible();

      await page.getByRole("button", { name: "docs" }).dblclick();
      await expect(page.getByRole("button", { name: "nested" })).toBeVisible();
      await expect(page.getByRole("button", { name: "a.txt" })).toBeVisible();

      await page.getByRole("button", { name: "nested" }).dblclick();
      await expect(page.getByRole("button", { name: "b.txt" })).toBeVisible();
      // 2단계까지 내려왔으므로 breadcrumb 에 경로가 남아 있어야 한다.
      await expect(page.getByRole("button", { name: "docs" })).toBeVisible();
    } finally {
      await purgeFolder(page, rootName);
      fs.rmSync(srcRoot, { recursive: true, force: true });
    }
  });
});
