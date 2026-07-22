// spec: 목록에서 "클릭으로 고르는 것"과 "체크박스로 담는 것"은 별개다 (frontend/src/lib/rowOpen.ts).
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/** 목록에서 폴더를 만든다. */
async function makeFolder(page: import("@playwright/test").Page, name: string) {
  await page.getByRole("button", { name: "새 폴더" }).click();
  await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
  await page.getByRole("textbox", { name: "폴더 이름" }).fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
  await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
}

/** 휴지통까지 비워 흔적을 남기지 않는다. */
async function purge(page: import("@playwright/test").Page, pattern: RegExp) {
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

test.describe("클릭과 체크박스 선택의 분리", () => {
  test("행을 클릭하거나 더블클릭해 폴더에 들어가도 체크가 켜지지 않는다", async ({ page }) => {
    const folderName = `e2e-select-${Date.now()}`;
    const pattern = new RegExp(folderName);

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, folderName);
      const row = page.getByRole("row", { name: pattern });
      const checkbox = page.getByRole("checkbox", { name: `${folderName} 선택` });
      const bar = page.getByTestId("selection-bar");

      // 1. 한 번 클릭 — 현재 항목이 될 뿐이다.
      await row.getByRole("button", { name: folderName }).click();

      // expect: 체크는 꺼진 채이고 선택 액션 바도 뜨지 않는다
      await expect(checkbox).not.toBeChecked();
      await expect(bar).toHaveCount(0);

      // 2. 더블클릭으로 폴더에 들어간다 — 이 동작이 체크를 남기던 것이 분리 이전 문제였다.
      await row.getByRole("button", { name: folderName }).dblclick();
      await expect(page.getByText("이 폴더가 비어 있습니다")).toBeVisible();

      // 3. 돌아와서 확인
      await page.goto("/");

      // expect: 체크는 여전히 꺼져 있다
      await expect(page.getByRole("checkbox", { name: `${folderName} 선택` })).not.toBeChecked();
      await expect(bar).toHaveCount(0);

      // 4. 담는 길은 체크박스뿐이다.
      await page.getByRole("checkbox", { name: `${folderName} 선택` }).check();

      // expect: 그제야 선택 액션 바가 뜬다
      await expect(bar).toContainText("1개 선택됨");
      await bar.getByRole("button", { name: "선택 해제" }).click();
      await expect(bar).toHaveCount(0);
    } finally {
      await purge(page, pattern);
    }
  });
});
