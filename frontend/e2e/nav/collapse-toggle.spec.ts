// spec: 좌측 네비게이션은 데스크톱에서 아이콘 레일로 접었다 펼 수 있고, 선택이 유지된다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

test.describe("좌측 네비게이션 접기", () => {
  test("접으면 아이콘 레일이 되고 새로고침해도 유지된다", async ({ page }) => {
    await loginAsAdmin(page);

    const sidebar = page.getByRole("complementary", { name: "주 메뉴" });
    await expect(sidebar).toHaveCSS("width", "240px");

    // 접기 → 아이콘 레일(64px). 라벨은 시각적으로만 숨고 접근성 이름은 남는다.
    await page.getByRole("button", { name: "메뉴 접기" }).click();
    await expect(sidebar).toHaveCSS("width", "64px");
    await expect(sidebar.getByRole("link", { name: "공유 링크" }).first()).toBeVisible();

    // 레일 상태에서도 네비게이션은 그대로 동작한다.
    await sidebar.getByRole("link", { name: "즐겨찾기" }).click();
    await expect(page).toHaveURL(/\/favorites$/);
    await expect(sidebar).toHaveCSS("width", "64px");

    // 새로고침해도 접힌 상태가 유지된다(localStorage).
    await page.reload();
    await expect(sidebar).toHaveCSS("width", "64px");

    // 다시 펼치면 원래 폭으로 돌아온다.
    await page.getByRole("button", { name: "메뉴 펼치기" }).click();
    await expect(sidebar).toHaveCSS("width", "240px");
  });
});
