// spec: spec/group-board.md — 게시판은 관리자가 만들고 **그룹을 붙여서 연다**. 붙이기 전에는
//       이름조차 내려가지 않고, 떼면 글은 남은 채 접근만 끊긴다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Locator, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";
import { ALL_USERS_GROUP } from "../support/boards";

/**
 * 관리 축 — 생성 → 할당 → 회수 → 삭제를 **전부 클릭으로** 지난다.
 *
 * 이 스펙의 값은 "게시판을 연다"가 그룹 할당 한 번이라는 것을 화면에서 확인하는 데 있다.
 * 할당 전 게시판은 사용자 목록(/boards)에 나오지 않는데, 그건 프런트가 숨기는 것이 아니라
 * 서버가 애초에 내려주지 않기 때문이다 — 그래서 "권한 없음" 같은 표시가 없는 것이 정상이다.
 *
 * 사이드바 "게시판" 항목이 **사라지는** 쪽은 단언하지 않는다. 이 계정에 다른 열린 게시판이
 * 하나라도 있으면 회수 후에도 항목이 남는 것이 맞고, 그 상태에서 실패하면 코드 회귀가 아니라
 * 데이터 때문이다(chat/session-crud.spec.ts 가 "목록이 비었다"를 피하는 것과 같은 이유).
 */

/** 관리 화면의 게시판 한 줄. 행에는 접근성 이름이 없어 카드 클래스로 잡는다. */
function adminRow(page: Page, name: string): Locator {
  return page.locator("li.card").filter({ hasText: name });
}

/** 사용자 목록(/boards)의 게시판 링크. 사이드바 항목과 이름이 겹치지 않게 href 로 좁힌다. */
function boardCard(page: Page, name: string): Locator {
  return page.locator('a[href^="/boards/"]').filter({ hasText: name });
}

test.describe("게시판 관리 — 그룹을 붙여 열고 떼어 닫는다", () => {
  test("생성 → @전사 할당 → 목록 노출 → 회수 → 삭제", async ({ page }) => {
    const name = `e2e-board-${Date.now()}`;

    // 삭제·회수 확인은 네이티브 window.confirm 이다(드라이브의 Modal 과 다르다).
    // Playwright 는 기본으로 dialog 를 dismiss 하므로 accept 를 걸어 두지 않으면
    // 삭제가 조용히 취소되고 "지웠는데 남아 있다"로 보인다.
    page.on("dialog", (d) => void d.accept());

    await loginAsAdmin(page);

    try {
      // 1. 생성 — 모달에 이름·설명을 넣는다.
      await page.goto("/admin/boards");
      await page.getByRole("button", { name: "게시판 추가" }).click();
      const modal = page.getByRole("dialog");
      await modal.getByLabel("이름").fill(name);
      await modal.getByLabel("설명 (선택)").fill("e2e 관리 축");
      await modal.getByRole("button", { name: "저장" }).click();

      const row = adminRow(page, name);
      await expect(row).toBeVisible();

      // 2. 갓 만든 게시판은 **아무에게도 보이지 않는다** — 그걸 화면이 먼저 말해 준다.
      await expect(row.getByText("그룹 미할당")).toBeVisible();
      await expect(row).toContainText("그룹 0 · 글 0");

      // 관리자 자신에게도 사용자 목록에는 나오지 않는다. 관리자는 게시판을 "읽을" 수 있지만
      // 그건 접근 가능한 게시판에 한한 이야기이고, 할당이 없으면 목록의 대상이 아니다.
      await page.goto("/boards");
      await expect(boardCard(page, name)).toHaveCount(0);

      // 3. 그룹 할당 — @전사 에 쓰기로 연다.
      await page.goto("/admin/boards");
      await adminRow(page, name).getByRole("button", { name: "그룹" }).click();
      const groupModal = page.getByRole("dialog");
      await expect(groupModal).toContainText("할당된 그룹이 없습니다");
      await groupModal.getByLabel("그룹").selectOption({ label: ALL_USERS_GROUP });
      await groupModal.getByLabel("권한").selectOption("write");
      await groupModal.getByRole("button", { name: "할당" }).click();

      // 할당된 그룹이 목록에 배지와 함께 선다.
      const grantRow = groupModal.locator("li").filter({ hasText: ALL_USERS_GROUP });
      await expect(grantRow).toBeVisible();
      await expect(grantRow.getByText("쓰기")).toBeVisible();
      // 모달에는 헤더 ✕(aria-label)와 푸터 버튼이 둘 다 "닫기"라 role 로 잡으면 모호하다.
      await groupModal.getByLabel("닫기").click();

      // 관리 목록의 요약도 따라 올라간다.
      await expect(adminRow(page, name).getByText("그룹 미할당")).toHaveCount(0);
      await expect(adminRow(page, name)).toContainText("그룹 1 · 글 0");

      // 4. 이제 사용자 목록에 뜬다 — 내 권한은 "쓰기"다.
      await page.goto("/boards");
      const card = boardCard(page, name);
      await expect(card).toBeVisible();
      await expect(card.getByText("쓰기")).toBeVisible();

      // 사이드바 항목도 나온다. /admin/boards 링크와 라벨이 같으므로 href 로 가른다.
      await expect(page.locator('a[href="/boards"]')).toBeVisible();

      // 게시판 안에서는 글쓰기 버튼이 열려 있다(write 라야 나온다).
      await card.click();
      await expect(page.getByRole("button", { name: "글쓰기" })).toBeVisible();

      // 5. 회수 — 접근이 끊기고 목록에서 사라진다.
      await page.goto("/admin/boards");
      await adminRow(page, name).getByRole("button", { name: "그룹" }).click();
      const revokeModal = page.getByRole("dialog");
      await revokeModal
        .locator("li")
        .filter({ hasText: ALL_USERS_GROUP })
        .getByRole("button", { name: "회수" })
        .click();
      await expect(revokeModal).toContainText("할당된 그룹이 없습니다");
      await revokeModal.getByLabel("닫기").click();

      await page.goto("/boards");
      await expect(boardCard(page, name)).toHaveCount(0);

      // 6. 삭제 — 관리 목록에서도 사라지고, 새로고침해도 돌아오지 않는다.
      await page.goto("/admin/boards");
      await adminRow(page, name).getByRole("button", { name: "게시판 삭제" }).click();
      await expect(adminRow(page, name)).toHaveCount(0);
      await page.reload();
      await expect(adminRow(page, name)).toHaveCount(0);
    } finally {
      // 중간 단언이 실패해 삭제까지 못 갔더라도 남겨 두지 않는다(best-effort, 멱등). 이름은
      // 활성 게시판에서만 점유되므로, 지워 두면 같은 이름으로 다시 돌려도 409 가 나지 않는다.
      await page.goto("/admin/boards");
      const leftover = adminRow(page, name);
      if (await existsNow(leftover)) {
        await leftover.getByRole("button", { name: "게시판 삭제" }).click();
        await expect(adminRow(page, name)).toHaveCount(0);
      }
    }
  });
});
