// spec: 좌측 네비게이션 섹션(드라이브/할 일/관리)은 라벨 클릭으로 접었다 펼 수 있고, 선택이 유지된다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

test.describe("네비게이션 섹션 접기", () => {
  test("섹션을 접으면 항목이 숨고 새로고침해도 유지된다", async ({ page }) => {
    await loginAsAdmin(page);

    const sidebar = page.getByRole("complementary", { name: "주 메뉴" });
    const driveSection = sidebar.getByRole("button", { name: "드라이브" });
    const todoSection = sidebar.getByRole("button", { name: "할 일" });

    // 기본은 모두 펼침 — 각 섹션의 항목이 보인다.
    await expect(driveSection).toHaveAttribute("aria-expanded", "true");
    await expect(sidebar.getByRole("link", { name: "내 드라이브" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "반복 루틴" })).toBeVisible();

    // 드라이브 섹션을 접으면 그 항목만 숨는다 — 다른 섹션은 그대로.
    await driveSection.click();
    await expect(driveSection).toHaveAttribute("aria-expanded", "false");
    await expect(sidebar.getByRole("link", { name: "내 드라이브" })).toBeHidden();
    await expect(sidebar.getByRole("link", { name: "반복 루틴" })).toBeVisible();

    // 할 일 섹션도 독립적으로 접힌다.
    await todoSection.click();
    await expect(sidebar.getByRole("link", { name: "반복 루틴" })).toBeHidden();

    // 새로고침해도 접힌 상태가 유지된다(localStorage).
    await page.reload();
    await expect(driveSection).toHaveAttribute("aria-expanded", "false");
    await expect(sidebar.getByRole("link", { name: "내 드라이브" })).toBeHidden();
    await expect(sidebar.getByRole("link", { name: "반복 루틴" })).toBeHidden();

    // 다시 펼치면 항목이 돌아온다.
    await driveSection.click();
    await todoSection.click();
    await expect(sidebar.getByRole("link", { name: "내 드라이브" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "반복 루틴" })).toBeVisible();
  });

  test("아이콘 레일 모드에서도 섹션 접기가 동작한다", async ({ page }) => {
    await loginAsAdmin(page);

    const sidebar = page.getByRole("complementary", { name: "주 메뉴" });

    // 헤더 햄버거로 레일 모드 진입.
    await page.getByRole("button", { name: "메뉴 접기" }).click();
    await expect(sidebar).toHaveCSS("width", "64px");

    // 레일에서도 섹션 아이콘을 눌러 접을 수 있다(접근성 이름은 sr-only 로 남는다).
    const driveSection = sidebar.getByRole("button", { name: "드라이브" });
    await driveSection.click();
    await expect(sidebar.getByRole("link", { name: "내 드라이브" })).toBeHidden();
    await driveSection.click();
    await expect(sidebar.getByRole("link", { name: "내 드라이브" })).toBeVisible();

    // 원상 복구 — 이후 테스트가 펼친 상태를 기대한다.
    await page.getByRole("button", { name: "메뉴 펼치기" }).click();
    await expect(sidebar).toHaveCSS("width", "240px");
  });
});
