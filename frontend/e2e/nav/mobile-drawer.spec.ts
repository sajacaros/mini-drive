// spec: 모바일 폭에서 좌측 네비게이션은 서랍(off-canvas)으로 동작하고, 본문이 가로로 넘치지 않는다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

// 아이폰 14 크기. 이 폭에서 240px 사이드바가 상주하면 본문에 150px 밖에 남지 않는다.
test.use({ viewport: { width: 390, height: 844 } });

const ROUTES = ["/", "/shares", "/trash", "/admin/users"];

test.describe("모바일 네비게이션", () => {
  test("햄버거로 서랍을 열고, 메뉴를 고르면 이동과 함께 닫힌다", async ({ page }) => {
    await loginAsAdmin(page);

    const sidebar = page.getByRole("complementary", { name: "주 메뉴" });
    // 기본은 닫힘 — 화면 밖에 있어야 본문이 온전한 폭을 쓴다.
    await expect(sidebar).not.toBeInViewport();

    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await expect(sidebar).toBeInViewport();

    // 메뉴 선택 → 이동 + 자동 닫힘(열린 채로 남으면 본문을 덮는다).
    await sidebar.getByRole("link", { name: "공유 링크" }).first().click();
    await expect(page).toHaveURL(/\/shares$/);
    await expect(sidebar).not.toBeInViewport();

    // 배경막을 눌러도 닫힌다.
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await expect(sidebar).toBeInViewport();
    await page.mouse.click(370, 400);
    await expect(sidebar).not.toBeInViewport();
  });

  test("주요 화면이 가로로 넘치지 않는다", async ({ page }) => {
    await loginAsAdmin(page);

    for (const route of ROUTES) {
      await page.goto(route);
      await expect(page.getByRole("button", { name: "메뉴 열기" })).toBeVisible();
      // 문서 자체가 가로로 스크롤되면(=사이드바가 자리를 차지하면) 본문이 화면 밖으로 밀린다.
      const overflow = await page.evaluate(() => {
        const de = document.documentElement;
        return de.scrollWidth - de.clientWidth;
      });
      expect(overflow, `${route} 에서 가로 오버플로`).toBeLessThanOrEqual(0);
    }
  });
});
