// spec: spec/wiki-index.md 「프런트」 — 위키는 드라이브의 한 항목이 아니라 자기 섹션이고,
//       질의(채팅)와 관리(문서 카탈로그)가 다른 화면으로 갈린다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

/**
 * 질의 진입점은 **하나**다. 한때 단발 질의(/wiki, "질문")와 대화(/chat, "채팅")를 사이드바에
 * 나란히 뒀지만, 물어보러 온 사람에게 둘의 차이는 눌러 보기 전에는 알 수 없었다. 대화가 단발
 * 질의를 포함하므로(한 번 묻고 닫으면 그만이다) 채팅 하나로 합쳤다.
 *
 * 합치면서 /wiki 주소는 **살려 뒀다** — 북마크와 옛 링크가 깨지지 않아야 한다. 사이드바에서
 * 사라진 화면은 아무도 눌러 보지 않으므로, 살아 있다는 것을 여기서 지킨다.
 */

test.describe("위키 네비게이션", () => {
  test("위키가 독립 섹션이고 채팅과 문서 카탈로그로 갈린다", async ({ page }) => {
    await loginAsAdmin(page);

    const sidebar = page.getByRole("complementary", { name: "주 메뉴" });

    // 위키는 드라이브 섹션 안이 아니라 자기 섹션이다 — 섹션 라벨은 접기 버튼이다.
    const wikiSection = sidebar.getByRole("button", { name: "위키" });
    await expect(wikiSection).toHaveAttribute("aria-expanded", "true");

    const chat = sidebar.getByRole("link", { name: "채팅" });
    const catalog = sidebar.getByRole("link", { name: "문서 카탈로그" });
    await expect(chat).toBeVisible();
    await expect(catalog).toBeVisible();
    // 질의 진입점은 하나다 — 옛 "질문" 항목이 되살아나면 다시 고르게 하는 셈이다.
    await expect(sidebar.getByRole("link", { name: "질문" })).toHaveCount(0);

    // 질의 화면 — 물어보는 데 필요한 것만 있다. 인덱싱된 문서 목록은 여기 없다.
    await chat.click();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByRole("heading", { name: "채팅" })).toBeVisible();
    await expect(page.getByPlaceholder("예: 2026년 계획을 작년과 비교해 주세요")).toBeVisible();
    await expect(page.getByRole("heading", { name: "문서 카탈로그" })).toBeHidden();

    // 관리 화면 — 무엇이 검색 대상인지는 이쪽이다.
    await catalog.click();
    await expect(page).toHaveURL(/\/wiki\/catalog$/);
    await expect(page.getByRole("heading", { name: "문서 카탈로그" })).toBeVisible();
    await expect(page.getByPlaceholder("예: 2026년 계획을 작년과 비교해 주세요")).toBeHidden();

    // 섹션을 접으면 두 항목이 함께 숨는다(다른 섹션과 같은 규약, localStorage 유지).
    await wikiSection.click();
    await expect(chat).toBeHidden();
    await expect(catalog).toBeHidden();
    await page.reload();
    await expect(catalog).toBeHidden();
    await wikiSection.click();
    await expect(catalog).toBeVisible();
  });

  test("사이드바에서 빠진 /wiki 주소는 그대로 열린다", async ({ page }) => {
    await loginAsAdmin(page);

    // 북마크·옛 링크로 들어오는 경로. 리다이렉트가 아니라 그 화면이 그대로 떠야 한다 —
    // 단발 질의는 세션을 만들지 않는 경로로 남겨 둔 것이라 /chat 으로 보내면 뜻이 달라진다.
    await page.goto("/wiki");
    await expect(page).toHaveURL(/\/wiki$/);
    await expect(page.getByRole("heading", { name: "위키", exact: true })).toBeVisible();
    await expect(page.getByPlaceholder("예: 배포 후 롤백은 어떤 기준으로 하나요?")).toBeVisible();
    await expect(page.getByRole("button", { name: "질문" })).toBeVisible();
  });
});
