// spec: backend/app/services/chat/sessions.py — 대화는 세션 단위로 쌓이고, 제목을 고치거나
//       지울 수 있다. 삭제는 소프트 삭제라 목록에서만 사라진다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Locator, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 세션 관리 축 — **모델을 부르지 않는다.** 질문을 보내지 않으므로 vLLM 이 꺼져 있어도 돈다.
 * 답변 품질과 근거는 `ask-and-cite.spec.ts` 가 본다.
 *
 * 계정에 이미 지난 대화가 쌓여 있어도 통과해야 한다 — "목록이 비었다"를 단언하면 이 계정으로
 * 한 번이라도 질문한 뒤부터 영영 실패한다. 그래서 전역 빈 상태 대신 **내가 만든 세션 한 줄**만
 * 보고 판정한다(새 세션은 목록 맨 앞에 붙는다 — ChatPage 의 onNewSession).
 */

/** 좌측 세션 목록. 사이드바(주 메뉴)도 같은 role 이라 "지난 대화" 제목으로 가른다. */
function sessionList(page: Page): Locator {
  return page.locator("aside").filter({ has: page.getByRole("heading", { name: "지난 대화" }) });
}

/**
 * 세션 한 줄. 제목·이름변경·삭제가 한 묶음인 행에는 접근성 이름이 없어 클래스로 잡는다
 * (favorites/grid-view-toggle.spec.ts 의 `div.group.card` 와 같은 방식).
 */
function sessionRows(page: Page): Locator {
  return sessionList(page).locator("div.group");
}

/** 제목으로 세션 한 줄을 찾는다. */
function sessionByTitle(page: Page, title: string): Locator {
  return sessionRows(page).filter({ hasText: title });
}

test.describe("채팅 세션 관리", () => {
  test("새 대화를 만들고 제목을 고친 뒤 지운다", async ({ page }) => {
    const title = `e2e-chat-${Date.now()}`;

    await loginAsAdmin(page);
    await page.goto("/chat");

    try {
      // 1. 새 대화 — 목록 맨 앞에 제목 없는 세션이 붙는다.
      await page.getByRole("button", { name: "새 채팅" }).click();
      const fresh = sessionRows(page).first();
      // 제목은 첫 질문이 지어 준다. 그전까지는 "제목 없음"이다 — 생성 버튼과 같은 글자를 쓰면
      // 목록의 유일한 항목이 버튼의 일부처럼 읽힌다(ChatPage 의 SessionRow 주석).
      await expect(fresh.getByRole("button", { name: "제목 없음" })).toBeVisible();

      // 대화가 없는 세션은 무엇을 하는 곳인지 말해 준다.
      await expect(page.getByText("무엇이든 물어보세요")).toBeVisible();

      // 2. 빈 질문은 보낼 수 없다 — 버튼이 잠겨 있어 헛 요청이 나가지 않는다.
      const send = page.getByRole("button", { name: "보내기" });
      await expect(send).toBeDisabled();
      const input = page.getByPlaceholder("예: 2026년 계획을 작년과 비교해 주세요");
      await input.fill("   ");
      await expect(send).toBeDisabled();
      await input.fill("");

      // 3. 제목 변경 — 아이콘은 hover·focus 에서만 드러나므로 행에 마우스를 올린다.
      await fresh.hover();
      await fresh.getByRole("button", { name: "제목 변경" }).click();
      const editor = sessionList(page).getByRole("textbox");
      await editor.fill(title);
      await editor.press("Enter");
      await expect(sessionByTitle(page, title)).toBeVisible();

      // 서버에 저장된 제목이다 — 새로고침해도 남는다(화면 state 만 바뀐 것이 아니다).
      await page.reload();
      await expect(sessionByTitle(page, title)).toBeVisible();

      // 4. Esc 로 취소하면 제목이 그대로다.
      const row = sessionByTitle(page, title);
      await row.hover();
      await row.getByRole("button", { name: "제목 변경" }).click();
      const editorAgain = sessionList(page).getByRole("textbox");
      await editorAgain.fill(`${title}-버려질제목`);
      await editorAgain.press("Escape");
      await expect(sessionByTitle(page, title)).toBeVisible();
      await expect(sessionByTitle(page, `${title}-버려질제목`)).toHaveCount(0);

      // 5. 삭제 — 목록에서 사라진다(서버는 소프트 삭제라 이력은 남는다).
      const doomed = sessionByTitle(page, title);
      await doomed.hover();
      await doomed.getByRole("button", { name: "대화 삭제" }).click();
      await expect(sessionByTitle(page, title)).toHaveCount(0);

      // 되살아나지 않는다 — 목록 조회에서 빠졌다는 뜻이다.
      await page.reload();
      await expect(sessionByTitle(page, title)).toHaveCount(0);
    } finally {
      // 중간 단언이 실패해 삭제까지 못 갔더라도 남겨 두지 않는다(best-effort, 멱등).
      await page.goto("/chat");
      const leftover = sessionByTitle(page, title);
      if (await existsNow(leftover)) {
        await leftover.hover();
        await leftover.getByRole("button", { name: "대화 삭제" }).click();
        await expect(sessionByTitle(page, title)).toHaveCount(0);
      }
    }
  });
});
