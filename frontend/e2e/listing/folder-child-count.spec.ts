// spec: spec/drive-ux-phase8.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 폴더 행의 "크기" 칸이 **직속 항목 수**를 말하는지 지킨다.
 *
 * 폴더는 크기가 없어 그 칸이 늘 "-" 로 비어 있었다. 대신 들어가 보지 않고도 안이 비었는지
 * 알 수 있게 항목 수를 넣었다(FileResponse.child_count).
 *
 * 재귀 합계가 아니라 직속 수라는 점을 하위 폴더로 함께 못 박는다 — 파일 3 + 하위 폴더 1 을
 * 넣으면 직속 기준으로는 4 이고, 재귀 합계로 바꾸면 이 수가 달라진다.
 */

/** 폴더를 휴지통 경유로 완전히 지운다(멱등). 안의 항목도 함께 사라진다. */
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

/** '새 폴더' 로 폴더를 만든다. 현재 보고 있는 폴더 아래에 생긴다. */
async function createFolder(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "새 폴더" }).click();
  await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
  await page.getByRole("textbox", { name: "폴더 이름" }).fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
  await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
}

test.describe("폴더 항목 수", () => {
  test("폴더 행에 직속 항목 수가 보이고, 비면 '비어 있음' 이다", async ({ page }) => {
    const stamp = Date.now();
    const folderName = `e2e-count-${stamp}`;
    const srcDir = path.join(os.tmpdir(), folderName);

    fs.mkdirSync(srcDir, { recursive: true });
    const files = Array.from({ length: 3 }, (_, i) => {
      const p = path.join(srcDir, `c${i}.txt`);
      fs.writeFileSync(p, `child ${i}`);
      return p;
    });

    try {
      await loginAsAdmin(page);
      await page.goto("/");
      await createFolder(page, folderName);

      // expect: 갓 만든 폴더는 "비어 있음". 예전처럼 "-" 면 안이 비었는지 알 수 없다.
      const row = page.getByRole("row", { name: new RegExp(folderName) });
      await expect(row).toContainText("비어 있음");

      // 폴더 안으로 들어가 파일 3개 + 하위 폴더 1개를 넣는다.
      await row.dblclick();
      await expect(page.getByRole("button", { name: folderName, exact: true })).toBeDisabled();

      const chooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      (await chooserPromise).setFiles(files);
      await expect(page.getByText("3개 파일을 업로드했습니다.")).toBeVisible();

      // 하위 폴더 안에도 파일이 있으면 재귀 합계와 직속 수가 갈린다 — 여기서는 안을 비워 둬도
      // 직속 4 / 재귀 4 가 같아지므로, 하위 폴더 자신이 1로 세어지는 것만 확인하면 충분하다.
      await createFolder(page, "안쪽");

      // expect: 부모 목록으로 돌아오면 파일 3 + 폴더 1 = 항목 4개.
      await page.goto("/");
      await expect(page.getByRole("row", { name: new RegExp(folderName) })).toContainText(
        "항목 4개",
      );
    } finally {
      await purgeFolder(page, folderName);
      fs.rmSync(srcDir, { recursive: true, force: true });
    }
  });
});
