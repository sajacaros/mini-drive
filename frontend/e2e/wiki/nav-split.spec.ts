// spec: spec/wiki-index.md 「프런트」 — 위키는 드라이브의 한 항목이 아니라 자기 섹션이고,
//       질의(질문)와 관리(문서 카탈로그)가 다른 화면으로 갈린다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

test.describe("위키 네비게이션", () => {
  test("위키가 독립 섹션이고 질문과 문서 카탈로그로 갈린다", async ({ page }) => {
    await loginAsAdmin(page);

    const sidebar = page.getByRole("complementary", { name: "주 메뉴" });

    // 위키는 드라이브 섹션 안이 아니라 자기 섹션이다 — 섹션 라벨은 접기 버튼이다.
    const wikiSection = sidebar.getByRole("button", { name: "위키" });
    await expect(wikiSection).toHaveAttribute("aria-expanded", "true");

    const ask = sidebar.getByRole("link", { name: "질문" });
    const catalog = sidebar.getByRole("link", { name: "문서 카탈로그" });
    await expect(ask).toBeVisible();
    await expect(catalog).toBeVisible();

    // 질문 화면 — 물어보는 데 필요한 것만 있다. 인덱싱된 문서 목록은 여기 없다.
    await ask.click();
    await expect(page.getByRole("heading", { name: "위키", exact: true })).toBeVisible();
    await expect(page.getByPlaceholder("예: 배포 후 롤백은 어떤 기준으로 하나요?")).toBeVisible();
    await expect(page.getByRole("button", { name: "질문" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "문서 카탈로그" })).toBeHidden();

    // 관리 화면 — 무엇이 검색 대상인지는 이쪽이다.
    await catalog.click();
    await expect(page).toHaveURL(/\/wiki\/catalog$/);
    await expect(page.getByRole("heading", { name: "문서 카탈로그" })).toBeVisible();
    await expect(page.getByPlaceholder("예: 배포 후 롤백은 어떤 기준으로 하나요?")).toBeHidden();

    // 섹션을 접으면 두 항목이 함께 숨는다(다른 섹션과 같은 규약, localStorage 유지).
    await wikiSection.click();
    await expect(ask).toBeHidden();
    await expect(catalog).toBeHidden();
    await page.reload();
    await expect(catalog).toBeHidden();
    await wikiSection.click();
    await expect(catalog).toBeVisible();
  });
});
