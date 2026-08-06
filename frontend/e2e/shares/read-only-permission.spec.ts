// spec: 읽기 전용(permission=read) 공유 링크는 다운로드 동선을 아예 노출하지 않고 열람만 준다.
// seed: frontend/e2e/support/shares.ts
//
// 백엔드는 다운로드 계열을 403 으로 막지만(tests/integration_shares.py), 화면에 버튼이 남아
// 있으면 누를 때마다 오류만 보게 된다 — 그 어긋남을 여기서 지킨다.

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { purgeSeededFile, seedShareLink } from "../support/shares";

const BODY = "읽기 전용 공유 미리보기 본문";

test.describe("읽기 전용 공유 링크", () => {
  test("공개 페이지에서 다운로드는 사라지고 미리보기는 열린다", async ({ page }) => {
    const fileName = `e2e-readonly-${Date.now()}.txt`;
    let fileId = 0;

    try {
      await loginAsAdmin(page);
      const seeded = await seedShareLink(page, fileName, "read", BODY);
      fileId = seeded.fileId;

      await page.goto(`/s/${seeded.shareUrl}`);
      await expect(page.getByText(fileName)).toBeVisible();

      // 다운로드 버튼은 없고, 무엇을 할 수 있는지는 문구로 알린다.
      await expect(page.getByRole("button", { name: "다운로드" })).toHaveCount(0);
      await expect(
        page.getByText("읽기 전용으로 공유된 파일입니다. 미리보기만 할 수 있습니다."),
      ).toBeVisible();

      // 열람은 read 권한이 주는 것 그 자체 — 미리보기는 그대로 열린다.
      await page.getByRole("button", { name: "미리보기" }).click();
      await expect(page.getByText(BODY)).toBeVisible();
      // 미리보기 창 안에도(헤더·폴백) 다운로드 동선은 없다.
      await expect(page.getByRole("button", { name: "다운로드" })).toHaveCount(0);
    } finally {
      if (fileId) await purgeSeededFile(page, fileId);
    }
  });

  test("다운로드 허용 공유는 그대로 다운로드 버튼을 준다", async ({ page }) => {
    const fileName = `e2e-downloadable-${Date.now()}.txt`;
    let fileId = 0;

    try {
      await loginAsAdmin(page);
      const seeded = await seedShareLink(page, fileName, "download", BODY);
      fileId = seeded.fileId;

      await page.goto(`/s/${seeded.shareUrl}`);
      await expect(page.getByRole("button", { name: "다운로드" })).toBeVisible();
      await expect(page.getByText("읽기 전용으로 공유된 파일입니다.")).toHaveCount(0);
    } finally {
      if (fileId) await purgeSeededFile(page, fileId);
    }
  });
});
