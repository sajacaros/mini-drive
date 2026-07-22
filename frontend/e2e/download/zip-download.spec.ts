// spec: spec/minidrive-prd.md 6.2 (POST /api/files/download-archive-ticket)
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/** 목록에서 폴더를 만든다. */
async function makeFolder(page: Page, name: string) {
  await page.getByRole("button", { name: "새 폴더" }).click();
  await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
  await page.getByRole("textbox", { name: "폴더 이름" }).fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
  await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
}

/** 로컬 임시 파일을 현재 폴더로 업로드하고, 행이 보일 때까지 기다린다. */
async function upload(page: Page, filePath: string) {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "업로드", exact: true }).click();
  await (await chooser).setFiles(filePath);
  const name = path.basename(filePath);
  await expect(page.getByRole("row", { name: new RegExp(name.replace(".", "\\.")) })).toBeVisible({
    timeout: 20_000,
  });
}

/** 휴지통까지 비워 흔적을 남기지 않는다. */
async function purge(page: Page, pattern: RegExp) {
  await page.goto("/");
  const row = page.getByRole("row", { name: pattern });
  if (await existsNow(row)) {
    await row.getByLabel("삭제").click();
    await page.getByRole("button", { name: "휴지통으로 이동" }).click();
    await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
  }
  await page.goto("/trash");
  const trashRow = page.getByRole("row", { name: pattern });
  if (await existsNow(trashRow)) {
    await trashRow.getByRole("button", { name: "영구 삭제" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
  }
}

test.describe("ZIP 다운로드", () => {
  test("폴더 행의 다운로드는 폴더 이름의 ZIP 을 내려준다", async ({ page }) => {
    const stamp = Date.now();
    const folderName = `e2e-zip-folder-${stamp}`;
    const fileName = `e2e-zip-inner-${stamp}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `zip download e2e ${stamp}`);

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, folderName);
      const folderRow = page.getByRole("row", { name: new RegExp(folderName) });
      // 폴더 안에 파일 하나를 넣어 둔다(빈 ZIP 이 아니라 내용이 실리는지 보기 위함).
      // 여는 조작은 더블클릭 대신 Enter 로 한다 — 목록이 비동기로 다시 그려지는 사이
      // 두 번째 클릭이 옆 행에 떨어지는 레이스를 피하려는 것(키 입력은 좌표가 없다).
      await folderRow.getByRole("button", { name: folderName }).press("Enter");
      // 빈 폴더 안내가 뜰 때까지 기다린다 — breadcrumb 만 보고 넘어가면 목록이 아직 이전
      // 폴더 내용을 그리고 있는 찰나에 걸린다(경로는 즉시, 목록은 비동기로 갱신된다).
      await expect(page.getByText("이 폴더가 비어 있습니다")).toBeVisible();
      await upload(page, filePath);

      await page.goto("/");
      const downloadPromise = page.waitForEvent("download");
      await page
        .getByRole("row", { name: new RegExp(folderName) })
        .getByLabel("ZIP 으로 다운로드")
        .click();
      const download = await downloadPromise;
      // expect: 폴더 이름 그대로의 zip 이 내려온다
      expect(download.suggestedFilename()).toBe(`${folderName}.zip`);
      const body = await download.createReadStream();
      const chunks: Buffer[] = [];
      for await (const chunk of body) chunks.push(chunk as Buffer);
      const zip = Buffer.concat(chunks);
      // expect: 로컬 파일 헤더(PK\x03\x04)로 시작하고 안에 파일 이름이 담겨 있다
      expect(zip.subarray(0, 4).toString("binary")).toBe("PK");
      expect(zip.includes(Buffer.from(fileName, "utf8"))).toBe(true);
    } finally {
      await purge(page, new RegExp(folderName));
      fs.rmSync(filePath, { force: true });
    }
  });

  test("체크박스로 파일과 폴더를 함께 골라 한 번에 내려받는다", async ({ page }) => {
    const stamp = Date.now();
    const folderName = `e2e-zip-multi-folder-${stamp}`;
    const fileName = `e2e-zip-multi-file-${stamp}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `multi select ${stamp}`);
    const names = [folderName, fileName];

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      // 업로드는 분당 한도(user 당 10회)가 있어 최소한만 쓴다 — 폴더 생성은 한도 밖이다.
      await makeFolder(page, folderName);
      await upload(page, filePath);

      // 1. 파일과 폴더의 선택 체크박스를 켠다
      for (const n of names) {
        await page.getByRole("checkbox", { name: `${n} 선택` }).check();
      }

      // expect: 선택 액션 바가 개수를 세어 보여준다
      const bar = page.getByTestId("selection-bar");
      await expect(bar).toContainText("2개 선택됨");

      // 2. 선택 액션 바의 다운로드
      const downloadPromise = page.waitForEvent("download");
      await bar.getByRole("button", { name: "다운로드" }).click();
      const download = await downloadPromise;

      // expect: 여럿이므로 날짜가 붙은 기본 이름의 zip 이 내려온다
      expect(download.suggestedFilename()).toMatch(/^flex-drive-\d{8}-\d{6}\.zip$/);
      const body = await download.createReadStream();
      const chunks: Buffer[] = [];
      for await (const chunk of body) chunks.push(chunk as Buffer);
      const zip = Buffer.concat(chunks);
      for (const n of names) {
        expect(zip.includes(Buffer.from(n, "utf8"))).toBe(true);
      }
    } finally {
      for (const n of names) await purge(page, new RegExp(n.replace(".", "\\.")));
      fs.rmSync(filePath, { force: true });
    }
  });
});
