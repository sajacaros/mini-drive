// spec: PDF 미리보기가 모달 안에서 **실제로 그려지고** 화면을 채우는지.
// seed: 이 파일 안에서 PDF 를 만들어 API 로 올린다(support/pdf.ts).

import { test, expect } from "@playwright/test";

import { distinctPixelCount, purgeFile, uploadPdf } from "../support/pdf";

/**
 * 예전에는 `<iframe src={blobURL}>` 로 브라우저 내장 뷰어에 맡겼고, 그래서 이 축을 검사할
 * 방법이 없었다 — 헤드리스 크로미움에는 PDF 플러그인이 없어 iframe 이 늘 빈 화면이라
 * "폴백 버튼이 있다" 정도만 단언할 수 있었다. 이제 pdf.js 가 canvas 에 직접 그리므로
 * 헤드리스에서도 진짜 렌더 결과를 볼 수 있다.
 *
 * 두 가지를 본다:
 *   1. canvas 에 실제 픽셀이 찍혔는가 (문서를 못 열면 빈 사각형이 남는다)
 *   2. 뷰어가 모달 높이를 실제로 채우는가 — iframe 시절 회귀가 정확히 이것이었다.
 *      부모가 높이를 안 주면 iframe 은 브라우저 기본값 150px 로 주저앉아 문서가 우표만 해졌다.
 */
test.describe("PDF 미리보기", () => {
  test("모달을 채우며 페이지가 canvas 로 그려진다", async ({ page }) => {
    const name = `e2e-pdf-${Date.now()}.pdf`;
    let fileId: number | null = null;

    await page.goto("/");
    try {
      fileId = await uploadPdf(page, name, ["Page One", "Page Two", "Page Three"]);
      await page.reload();

      await page.getByRole("button", { name }).first().click();
      await page.getByRole("button", { name: "미리보기" }).click();

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();

      // expect: 첫 페이지가 그려진다.
      const firstPage = dialog.locator('[data-page="1"] canvas');
      await expect(firstPage).toBeVisible({ timeout: 30_000 });
      expect(await distinctPixelCount(page, '[data-page="1"] canvas')).toBeGreaterThan(1);

      // expect: 페이지 수를 안다(문서를 끝까지 파싱했다는 뜻).
      await expect(dialog.getByText("1 / 3")).toBeVisible();

      // expect: 뷰어가 모달 높이의 대부분을 쓴다. 150px 로 주저앉던 회귀를 여기서 잡는다.
      const box = await firstPage.boundingBox();
      const dialogBox = await dialog.boundingBox();
      expect(box).not.toBeNull();
      expect(dialogBox).not.toBeNull();
      expect(box!.height).toBeGreaterThan(400);
      // 모달 자체도 화면을 채워야 한다(뷰어형 내용일 때 카드가 실제 높이를 받는다).
      expect(dialogBox!.height).toBeGreaterThan(page.viewportSize()!.height * 0.8);

      // expect: 폭 맞춤이 기본이라 페이지가 모달 폭을 거의 다 쓴다.
      expect(box!.width).toBeGreaterThan(dialogBox!.width * 0.8);

      // expect: 확대하면 페이지가 실제로 커진다.
      // 배율 버튼의 접근성 이름은 현재 배율(텍스트)이라 역할+이름으로는 못 잡는다 — title 로 찾는다.
      const zoomLevel = dialog.getByTitle("폭 맞춤");
      await expect(zoomLevel).toHaveText("맞춤");
      await dialog.getByRole("button", { name: "확대" }).click();
      await expect(zoomLevel).toHaveText("125%");
      await expect
        .poll(async () => (await firstPage.boundingBox())!.width, { timeout: 15_000 })
        .toBeGreaterThan(box!.width * 1.1);

      // expect: 내장 뷰어로 빠져나가는 길이 늘 있다(텍스트 검색·인쇄가 필요한 사람용).
      await expect(dialog.getByRole("button", { name: "새 탭에서 열기" })).toBeVisible();
    } finally {
      if (fileId !== null) await purgeFile(page, fileId);
    }
  });

  test("긴 문서를 끝까지 넘겨도 canvas 가 쌓이지 않는다", async ({ page }) => {
    // canvas 는 화면에서 사라져도 폭×높이×DPR²×4바이트를 계속 붙들고 있다. 전 페이지를 남겨
    // 두면 수십 장짜리 덱 하나로 수백 MB 가 쌓여 휴대폰이 죽는다 — 뷰포트에서 멀어진 페이지는
    // 놓아 줘야 한다. "보이는 페이지는 그려진다"와 "안 보이는 페이지는 없다"를 함께 본다.
    const name = `e2e-pdf-long-${Date.now()}.pdf`;
    const pages = Array.from({ length: 30 }, (_, i) => `Slide ${i + 1}`);
    let fileId: number | null = null;

    await page.goto("/");
    try {
      fileId = await uploadPdf(page, name, pages);
      await page.reload();

      await page.getByRole("button", { name }).first().click();
      await page.getByRole("button", { name: "미리보기" }).click();

      const dialog = page.getByRole("dialog");
      const canvases = dialog.locator("canvas");
      await expect(dialog.locator('[data-page="1"] canvas')).toBeVisible({ timeout: 30_000 });
      await expect(dialog.getByText("1 / 30")).toBeVisible();

      // expect: 30 장짜리인데 처음부터 다 그리지는 않는다.
      expect(await canvases.count()).toBeLessThan(10);

      // 문서 끝까지 스크롤.
      const scroller = dialog.locator('[data-page="1"]').locator("xpath=../..");
      await scroller.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));

      // expect: 마지막 페이지가 그려지고, 지나온 페이지는 회수됐다.
      await expect(dialog.locator('[data-page="30"] canvas')).toBeVisible({ timeout: 30_000 });
      await expect.poll(async () => canvases.count(), { timeout: 15_000 }).toBeLessThan(10);
      await expect(dialog.locator('[data-page="1"] canvas')).toHaveCount(0);

      // expect: canvas 를 놓아 줘도 자리는 남는다. 자리표시자 없이 회수하면 문서가 접히면서
      // 스크롤 위치가 위로 튀어 오른다 — 회수보다 그 쪽이 더 눈에 띄는 고장이다.
      const releasedHeight = await dialog
        .locator('[data-page="1"]')
        .evaluate((el) => el.getBoundingClientRect().height);
      expect(releasedHeight).toBeGreaterThan(300);
      const scrollTop = await scroller.evaluate((el) => el.scrollTop);
      expect(scrollTop).toBeGreaterThan(page.viewportSize()!.height * 20);
    } finally {
      if (fileId !== null) await purgeFile(page, fileId);
    }
  });
});
