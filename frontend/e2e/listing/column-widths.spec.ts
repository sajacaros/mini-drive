// spec: spec/drive-ux-phase8.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 목록 표의 열 폭 규칙을 지킨다 (frontend/src/lib/tableCols.ts).
 *
 * 예전에는 이름 열만 폭이 없어서 남는 공간을 혼자 다 먹었다. 그 결과 창을 넓혀도 크기·수정일
 * 칸은 그대로라 "항목 12개"·"2026.07.30 19:24" 가 두 줄로 접혔다 — 오른쪽은 텅 빈 채로.
 * 그래서 (a) 모든 열에 폭을 줘서 남는 공간이 비율대로 나뉘게 하고, (b) 값이 두 동강 나면
 * 안 되는 크기·수정일 칸에 nowrap 을 걸었다. 이 둘을 각각 못 박는다.
 */

/** 폴더 행 하나를 확보한다(없으면 만든다). 폴더여야 "항목 N개" 가 크기 칸에 나온다. */
async function ensureFolder(page: Page, name: string): Promise<void> {
  await page.goto("/");
  if (await existsNow(page.getByRole("row", { name: new RegExp(name) }))) return;
  await page.getByRole("button", { name: "새 폴더" }).click();
  await page.getByRole("textbox", { name: "폴더 이름" }).fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
  await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
}

/** 헤더 셀의 실제 렌더 폭. */
async function headerWidth(page: Page, name: string): Promise<number> {
  return page
    .getByRole("columnheader", { name })
    .first()
    .evaluate((el) => el.getBoundingClientRect().width);
}

test.describe("목록 표 열 폭", () => {
  test("창이 넓어지면 이름 말고 다른 열도 같이 넓어진다", async ({ page }) => {
    await loginAsAdmin(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("columnheader", { name: "수정일" })).toBeVisible();
    const narrow = {
      date: await headerWidth(page, "수정일"),
      owner: await headerWidth(page, "소유자"),
    };

    await page.setViewportSize({ width: 2200, height: 900 });
    // 레이아웃이 다시 잡힐 때까지 — 폭이 실제로 변한 것으로 판정한다.
    await expect
      .poll(() => headerWidth(page, "수정일"))
      .toBeGreaterThan(narrow.date + 20);
    expect(await headerWidth(page, "소유자")).toBeGreaterThan(narrow.owner + 20);
  });

  test("크기·수정일 값은 한 줄로 나온다", async ({ page }) => {
    const folderName = `e2e-col-${Date.now()}`;
    await loginAsAdmin(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await ensureFolder(page, folderName);

    const row = page.getByRole("row", { name: new RegExp(folderName) });
    const cells = row.getByRole("cell");
    /*
      크기(폴더면 "비어 있음"/"항목 N개")와 수정일 칸이 각각 한 줄인지.
      칸 높이로는 못 잰다 — 행 높이는 이름 칸의 썸네일(32px)이 정하므로 늘 두 줄처럼 나온다.
      대신 Range 의 줄 상자(client rect) 개수를 센다.
    */
    for (const idx of [5, 6]) {
      const lines = await cells.nth(idx).evaluate((el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        return range.getClientRects().length;
      });
      expect(lines).toBe(1);
    }

    // 뒷정리 — 이 스펙이 만든 폴더만 지운다.
    await row.getByLabel("삭제").click();
    await page.getByRole("button", { name: "휴지통으로 이동" }).click();
    await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
    await page.goto("/trash");
    const trashRow = page.getByRole("row", { name: new RegExp(folderName) });
    await trashRow.getByRole("button", { name: "영구 삭제" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
  });
});
