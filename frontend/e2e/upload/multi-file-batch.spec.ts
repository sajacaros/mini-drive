// spec: spec/folder-upload-batch.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 여러 파일을 한 번에 고르면 **배치 한 요청**으로 나가는지 지킨다.
 *
 * 예전에는 파일마다 `POST /api/files/upload` 를 하나씩 보냈고, 업로드 rate limit 이 user 당
 * 분당 10회라 11번째 파일부터 429 로 실패했다(재시도도 없었다). 같은 파일들을 폴더로 끌면
 * 배치로 나가 멀쩡했으니, 사용자 입장에서는 고르는 방법에 따라 되고 안 되는 상태였다.
 *
 * E2E 스택은 rate limiting 을 꺼 두므로(e2e/support/stack.ts) 429 자체를 재현할 수는 없다.
 * 대신 **한도를 무의미하게 만드는 불변식** — "파일 N개 = 요청 1개" — 을 네트워크에서 직접
 * 센다. 단일 업로드 경로로 되돌아가면 이 카운트가 즉시 깨진다.
 */

const FILE_COUNT = 12; // rate_limit_upload_per_min(10) 을 넘겨야 회귀를 잡는다.

/** 업로드 대상 폴더를 휴지통 경유로 완전히 지운다(멱등). 안의 파일도 함께 사라진다. */
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

test.describe("여러 파일 업로드", () => {
  test(`파일 ${FILE_COUNT}개를 한 번에 골라도 배치 한 요청으로 나간다`, async ({ page }) => {
    const stamp = Date.now();
    const folderName = `e2e-multi-${stamp}`;
    const srcDir = path.join(os.tmpdir(), folderName);

    fs.mkdirSync(srcDir, { recursive: true });
    const files = Array.from({ length: FILE_COUNT }, (_, i) => {
      const p = path.join(srcDir, `f${i}.txt`);
      fs.writeFileSync(p, `multi upload ${i}`);
      return p;
    });

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 업로드 대상 폴더를 만들고 그 안으로 들어간다 — 정리를 폴더 하나로 끝내기 위해서다.
      await page.getByRole("button", { name: "새 폴더" }).click();
      await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
      await page.getByRole("textbox", { name: "폴더 이름" }).fill(folderName);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
      await page.getByRole("row", { name: new RegExp(folderName) }).dblclick();
      // 진입 확인 — breadcrumb 의 마지막 조각(현재 폴더)은 비활성 버튼이다.
      await expect(page.getByRole("button", { name: folderName, exact: true })).toBeDisabled();

      // 2. 요청을 센다. 파일 선택 이후에 나가는 것만 보면 되므로 여기서 붙인다.
      let singleRequests = 0;
      let batchRequests = 0;
      page.on("request", (req) => {
        if (req.method() !== "POST") return;
        const url = new URL(req.url()).pathname;
        if (url.endsWith("/api/files/upload")) singleRequests += 1;
        if (url.endsWith("/api/files/batch")) batchRequests += 1;
      });

      // 3. "업로드" 버튼(webkitdirectory 없는 multiple input)으로 파일 12개를 한 번에 고른다.
      const chooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const chooser = await chooserPromise;
      expect(chooser.isMultiple()).toBe(true);
      await chooser.setFiles(files);

      // expect: 12개를 한 번에 올렸다는 집계 토스트 하나 (파일마다 하나가 아니다).
      await expect(page.getByText(`${FILE_COUNT}개 파일을 업로드했습니다.`)).toBeVisible();

      // expect: 12개 행이 모두 목록에 있다 — 429 로 조용히 빠진 파일이 없다.
      for (const f of files) {
        await expect(page.getByRole("row", { name: new RegExp(path.basename(f)) })).toBeVisible();
      }

      // expect(핵심): 단일 업로드는 0회, 배치는 1회. 12개가 한 요청에 담겼다.
      expect(singleRequests).toBe(0);
      expect(batchRequests).toBe(1);
    } finally {
      await purgeFolder(page, folderName);
      fs.rmSync(srcDir, { recursive: true, force: true });
    }
  });
});
