// spec: spec/minidrive-prd.md 6.2 (PUT /api/files/{id})
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

/** 제자리 편집 입력은 한 번에 하나만 열리므로 페이지 범위로 잡아도 모호하지 않다. */
function nameInput(page: import("@playwright/test").Page) {
  return page.getByRole("textbox", { name: "이름" });
}

test.describe("이름 제자리 편집", () => {
  test("선택 후 F2 로 이름을 고치고 Enter 로 확정한다", async ({ page }) => {
    const stamp = Date.now();
    const before = `e2e-rename-f2-${stamp}`;
    const after = `e2e-renamed-f2-${stamp}`;

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, before);
      const row = page.getByRole("row", { name: new RegExp(before) });

      // 1. 한 번 클릭 = 선택. 이 상태에서 F2 가 편집을 연다.
      await row.getByText(before).click();
      await page.keyboard.press("F2");

      // expect: 행 자리에 입력이 뜨고 원래 이름이 들어 있다
      await expect(nameInput(page)).toHaveValue(before);

      await nameInput(page).fill(after);
      await nameInput(page).press("Enter");

      // expect: 토스트 + 목록의 이름이 바뀐다
      await expect(page.getByText("이름을 변경했습니다.")).toBeVisible();
      await expect(page.getByRole("row", { name: new RegExp(after) })).toBeVisible();
      await expect(row).not.toBeVisible();
    } finally {
      await purge(page, new RegExp(before));
      await purge(page, new RegExp(after));
    }
  });

  test("Esc 로 취소하면 이름이 그대로 남는다", async ({ page }) => {
    const stamp = Date.now();
    const name = `e2e-rename-esc-${stamp}`;

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, name);
      const row = page.getByRole("row", { name: new RegExp(name) });

      await row.getByText(name).click();
      await page.keyboard.press("F2");
      await nameInput(page).fill(`${name}-버려질이름`);
      await nameInput(page).press("Escape");

      // expect: 입력이 닫히고 원래 이름 행이 그대로 있다
      await expect(nameInput(page)).toHaveCount(0);
      await expect(row).toBeVisible();
      await expect(page.getByRole("row", { name: /버려질이름/ })).toHaveCount(0);
    } finally {
      await purge(page, new RegExp(name));
    }
  });

  test("그리드 보기에서도 카드 자리에서 이름을 고친다", async ({ page }) => {
    const stamp = Date.now();
    const before = `e2e-rename-grid-${stamp}`;
    const after = `e2e-renamed-grid-${stamp}`;

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, before);
      await page.getByRole("button", { name: "그리드 보기" }).click();

      // 카드 이름을 클릭해 선택한 뒤 F2
      await page.getByText(before, { exact: true }).click();
      await page.keyboard.press("F2");
      await expect(nameInput(page)).toHaveValue(before);

      await nameInput(page).fill(after);
      await nameInput(page).press("Enter");

      await expect(page.getByText("이름을 변경했습니다.")).toBeVisible();
      await expect(page.getByText(after, { exact: true })).toBeVisible();
    } finally {
      // 이후 테스트가 목록 보기를 전제하므로 되돌린다(보기 설정은 localStorage 에 남는다).
      await page.getByRole("button", { name: "목록 보기" }).click();
      await purge(page, new RegExp(before));
      await purge(page, new RegExp(after));
    }
  });

  test("선택된 항목의 이름을 한 번 더 클릭하면 편집으로 들어간다", async ({ page }) => {
    const stamp = Date.now();
    const name = `e2e-rename-click-${stamp}`;

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, name);
      const row = page.getByRole("row", { name: new RegExp(name) });

      // 첫 클릭은 선택. 두 번째 클릭이 더블클릭(=열기)으로 묶이지 않도록 간격을 둔다.
      await row.getByText(name).click();
      await page.waitForTimeout(700);
      await row.getByText(name).click();

      // expect: 폴더로 들어가지 않고 제자리 편집이 열린다
      await expect(nameInput(page)).toHaveValue(name);
      await expect(page.getByRole("button", { name: "내 드라이브" })).toBeVisible();

      await nameInput(page).press("Escape");
    } finally {
      await purge(page, new RegExp(name));
    }
  });
});
