// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

test.describe("즐겨찾기", () => {
  test("즐겨찾기 빈 상태 확인", async ({ page }) => {
    // 1. loginAsAdmin으로 로그인 후 사이드바 '즐겨찾기' 링크(→ /favorites)로 이동.
    //    사전에 즐겨찾기한 항목이 없는 상태를 보장.
    await loginAsAdmin(page);
    await page.getByRole("link", { name: "즐겨찾기" }).click();
    await expect(page).toHaveURL(/\/favorites$/);

    // expect: heading "즐겨찾기"와 부제 "별표한 파일과 폴더입니다."가 보인다
    await expect(page.getByRole("heading", { name: "즐겨찾기" })).toBeVisible();
    await expect(page.getByText("별표한 파일과 폴더입니다.")).toBeVisible();

    // expect: 빈 상태 문구 "즐겨찾기한 항목이 없습니다" /
    // "파일이나 폴더의 별 아이콘을 눌러 즐겨찾기에 추가하세요."가 보인다
    await expect(page.getByText("즐겨찾기한 항목이 없습니다")).toBeVisible();
    await expect(
      page.getByText("파일이나 폴더의 별 아이콘을 눌러 즐겨찾기에 추가하세요."),
    ).toBeVisible();
  });
});
