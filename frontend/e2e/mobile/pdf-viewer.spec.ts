// spec: 휴대폰(390×844)에서 PDF 가 백지가 아니라 실제로 보이는지.
// seed: 이 파일 안에서 PDF 를 만들어 API 로 올린다(support/pdf.ts).

import { test, expect } from "@playwright/test";

import { distinctPixelCount, purgeFile, uploadPdf } from "../support/pdf";

/**
 * 이게 이 변경의 핵심 회귀다. 내장 뷰어에 맡기던 시절 휴대폰에서는 늘 백지였다 —
 * Android Chrome 은 iframe 안의 PDF 를 인라인으로 렌더하지 않고, iOS Safari 는 blob 을 물린
 * iframe 을 자주 빈 화면으로 그린다. pdf.js 는 blob URL 을 거치지 않고 바이트를 직접 받으므로
 * 그 제약을 비켜간다.
 *
 * 엔진은 크로미움이라(playwright.config.ts 의 mobile 프로젝트 주석 참고) iOS Safari 의
 * 그 버그 자체를 여기서 재현하지는 못한다. 다만 "좁은 화면에서 뷰어가 전체 화면으로 열리고
 * 페이지가 폭에 맞게 그려진다"는 우리 쪽 계약은 여기서 지켜진다.
 */
test.describe("모바일 PDF 미리보기", () => {
  test("전체 화면으로 열리고 페이지가 폭에 맞게 그려진다", async ({ page }) => {
    const name = `e2e-mobile-pdf-${Date.now()}.pdf`;
    let fileId: number | null = null;

    await page.goto("/");
    try {
      fileId = await uploadPdf(page, name, ["Mobile One", "Mobile Two"]);
      await page.reload();

      await page.getByRole("button", { name }).first().click();
      await page.getByRole("button", { name: "미리보기" }).click();

      const dialog = page.getByRole("dialog");
      const firstPage = dialog.locator('[data-page="1"] canvas');
      await expect(firstPage).toBeVisible({ timeout: 30_000 });

      // expect: 백지가 아니다 — 이 단언이 곧 "휴대폰에서 PDF 가 보인다".
      expect(await distinctPixelCount(page, '[data-page="1"] canvas')).toBeGreaterThan(1);

      const viewport = page.viewportSize()!;
      const box = (await firstPage.boundingBox())!;
      // expect: 화면 폭을 (여백만 빼고) 다 쓴다.
      expect(box.width).toBeGreaterThan(viewport.width * 0.85);
      // expect: 좁은 화면에서는 모달이 전체 화면이라 뷰어도 세로로 넉넉하다.
      expect(box.height).toBeGreaterThan(300);

      // expect: 가로로 넘치지 않는다.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBe(0);
    } finally {
      if (fileId !== null) await purgeFile(page, fileId);
    }
  });
});
