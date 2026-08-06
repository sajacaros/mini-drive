// spec: spec/group-board.md — 글은 마크다운이고, 댓글은 수정 없이 지우면 자리만 남으며,
//       첨부는 드라이브와 분리돼 글에만 붙는다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Locator, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { purgeBoard, seedOpenBoard } from "../support/boards";

/**
 * 글 축 — 쓰기·수정·첨부·댓글·삭제를 한 글에서 이어서 본다.
 *
 * 게시판 자체는 API 로 시드한다(support/boards.ts). 관리 화면 클릭은
 * `admin-open-and-close.spec.ts` 가 이미 보므로 여기서 되풀이하면 실패 지점만 늘어난다.
 *
 * 댓글 삭제 뒤 **자리가 남는지**가 이 스펙의 핵심 단언이다. 소프트 삭제로 "삭제된 댓글입니다"를
 * 남기는 이유가 아래 답글이 대화 맥락을 잃지 않게 하는 것이라, 행이 통째로 사라지면 그 의도가
 * 깨진 것이다.
 */

/** 글 목록의 글 한 줄. 사이드바·헤더와 겹치지 않게 글 상세 링크로 좁힌다. */
function postRow(page: Page, title: string): Locator {
  return page.locator('a[href*="/posts/"]').filter({ hasText: title });
}

/** 댓글 한 줄. 댓글 영역은 카드 목록이라 본문 텍스트로 가른다. */
function commentCard(page: Page, text: string): Locator {
  return page.locator("li.card").filter({ hasText: text });
}

test.describe("게시판 글 — 쓰고 고치고 붙이고 지운다", () => {
  test("글쓰기 → 마크다운 렌더 → 첨부 → 댓글 → 수정 → 삭제", async ({ page }) => {
    const stamp = Date.now();
    const boardName = `e2e-posts-${stamp}`;
    const title = `e2e-글-${stamp}`;
    let boardId: number | null = null;

    // 글·첨부·댓글 삭제가 전부 네이티브 confirm 을 탄다.
    page.on("dialog", (d) => void d.accept());

    await loginAsAdmin(page);

    try {
      boardId = await seedOpenBoard(page, boardName);
      await page.goto(`/boards/${boardId}`);

      // 시드가 write 로 열었으므로 글쓰기가 열려 있다. 비어 있는 게시판은 그렇다고 말해 준다.
      await expect(page.getByText("아직 글이 없습니다")).toBeVisible();
      await page.getByRole("button", { name: "글쓰기" }).click();

      // 1. 본문은 마크다운이다 — 올리기 전에 미리보기 탭에서 렌더를 확인할 수 있다.
      await page.getByPlaceholder("제목").fill(title);
      const bodyInput = page.getByPlaceholder(/본문 \(마크다운\)/);
      await bodyInput.fill(`## ${title} 소제목\n\n본문 첫 줄입니다.`);
      await page.getByRole("button", { name: "미리보기" }).click();
      // components/Markdown.tsx 는 소제목을 h2 가 아니라 굵은 <p> 로 낸다. 그래서 role 이
      // 아니라 "'##' 마커가 사라지고 글자만 남았는가"로 렌더를 판정한다.
      await expect(page.getByText(`${title} 소제목`, { exact: true })).toBeVisible();
      await expect(page.getByText(`## ${title} 소제목`)).toHaveCount(0);

      // 2. 올리면 그 글의 상세로 바로 넘어간다.
      await page.getByRole("button", { name: "올리기" }).click();
      await expect(page).toHaveURL(/\/boards\/\d+\/posts\/\d+$/);
      await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
      // 저장된 본문도 마크다운으로 렌더된다(작성 중 미리보기와 같은 렌더러다).
      await expect(page.getByText(`${title} 소제목`, { exact: true })).toBeVisible();

      // 3. 첨부 — 드라이브가 아니라 이 글에 붙는다. 붙기 전에는 그렇다고 적혀 있다.
      await expect(page.getByText("첨부가 없습니다.", { exact: false })).toBeVisible();
      const chooser = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "파일 추가" }).click();
      await (
        await chooser
      ).setFiles({
        name: "e2e-첨부.txt",
        mimeType: "text/plain",
        buffer: Buffer.from(`board attachment ${stamp}`),
      });
      const attachment = page.locator("li.card").filter({ hasText: "e2e-첨부.txt" });
      await expect(attachment).toBeVisible();

      // 목록으로 나가면 첨부 수가 함께 보인다.
      await page.goto(`/boards/${boardId}`);
      await expect(postRow(page, title)).toContainText("첨부 1");

      // 4. 댓글 — write 라야 입력칸이 나온다. 등록하면 목록 끝에 붙는다.
      await postRow(page, title).click();
      await expect(page.getByText("아직 댓글이 없습니다")).toBeVisible();
      const commentBody = `e2e-댓글-${stamp}`;
      await page.getByPlaceholder(/댓글을 남기세요/).fill(commentBody);
      await page.getByRole("button", { name: "댓글 등록" }).click();
      await expect(commentCard(page, commentBody)).toBeVisible();

      // 글 목록에 댓글 수가 동봉된다.
      await page.goto(`/boards/${boardId}`);
      await expect(postRow(page, title)).toContainText("[1]");
      await postRow(page, title).click();

      // 5. 댓글 삭제 — 지워도 **자리는 남는다**(소프트 삭제).
      await commentCard(page, commentBody).getByRole("button", { name: "댓글 삭제" }).click();
      await expect(commentCard(page, commentBody)).toHaveCount(0);
      await expect(commentCard(page, "삭제된 댓글입니다.")).toBeVisible();

      // 6. 글 수정 — 작성자 본인이라 수정 버튼이 있고, 고치면 "(수정됨)"이 붙는다.
      // exact 를 켜야 한다 — 기본 부분일치면 "첨부 삭제"·"댓글 삭제" 까지 걸린다.
      await page.getByRole("button", { name: "수정", exact: true }).click();
      const editedTitle = `${title}-수정`;
      await page.locator("input.input").first().fill(editedTitle);
      await page.getByRole("button", { name: "저장", exact: true }).click();
      await expect(page.getByRole("heading", { name: editedTitle, level: 1 })).toBeVisible();
      await expect(page.getByText("(수정됨)")).toBeVisible();

      // 새로고침해도 남는다 — 화면 state 가 아니라 서버가 바뀐 것이다.
      await page.reload();
      await expect(page.getByRole("heading", { name: editedTitle, level: 1 })).toBeVisible();

      // 7. 첨부 개별 제거.
      await page
        .locator("li.card")
        .filter({ hasText: "e2e-첨부.txt" })
        .getByRole("button", { name: "첨부 삭제" })
        .click();
      await expect(page.locator("li.card").filter({ hasText: "e2e-첨부.txt" })).toHaveCount(0);

      // 8. 글 삭제 — 게시판 목록으로 돌아오고 그 글은 없다.
      await page.getByRole("button", { name: "삭제", exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/boards/${boardId}$`));
      await expect(postRow(page, editedTitle)).toHaveCount(0);
      await expect(page.getByText("아직 글이 없습니다")).toBeVisible();
    } finally {
      // 게시판을 지우면 그 아래 글·댓글·첨부가 한 번에 정리된다(best-effort, 멱등).
      if (boardId !== null) await purgeBoard(page, boardId);
    }
  });
});
