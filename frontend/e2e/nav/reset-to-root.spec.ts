// spec: 좌측 네비게이션은 어디에 있든 해당 메뉴의 루트로 이동한다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 좌측 네비 클릭 = 그 메뉴의 루트로 복귀.
 *
 * 페이지들이 화면 안 위치(현재 폴더, 목록 페이지 번호)를 URL 이 아니라 컴포넌트 state 로
 * 들고 있어서, 같은 경로로 이동하면 리마운트가 없어 화면이 그대로 남는 문제가 있었다.
 * Layout 이 Outlet 을 location.key 로 리마운트해 해결한다.
 */

async function purgeFolder(page: Page, folderName: string): Promise<void> {
  const namePattern = new RegExp(folderName);

  await page.goto("/");
  const row = page.getByRole("row", { name: namePattern });
  if (await existsNow(row)) {
    await row.getByLabel("삭제").click();
    await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
    await page.getByRole("button", { name: "휴지통으로 이동" }).click();
    await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
    await expect(row).not.toBeVisible();
  }

  await page.goto("/trash");
  const trashRow = page.getByRole("row", { name: namePattern });
  if (await existsNow(trashRow)) {
    await trashRow.getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
    await expect(trashRow).not.toBeVisible();
  }
}

test.describe("좌측 네비게이션", () => {
  test("폴더를 파고든 뒤 '내 드라이브'를 누르면 루트로 돌아온다", async ({ page }) => {
    const outer = `e2e-nav-${Date.now()}`;
    const inner = "안쪽";

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 2단계 폴더를 만들어 들어간다.
      await page.getByRole("button", { name: "새 폴더" }).click();
      await page.getByPlaceholder("폴더 이름").fill(outer);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();

      await page.getByRole("button", { name: outer }).click();
      await page.getByRole("button", { name: "새 폴더" }).click();
      await page.getByPlaceholder("폴더 이름").fill(inner);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();

      await page.getByRole("button", { name: inner }).click();
      // 2단계 안이므로 breadcrumb 에 두 폴더가 모두 남아 있다.
      await expect(page.getByRole("button", { name: outer })).toBeVisible();

      // 핵심: URL 이 이미 "/" 인 상태에서 "내 드라이브" 를 누른다.
      await page.getByRole("link", { name: "내 드라이브" }).click();

      // 루트로 복귀 — 최상위 폴더가 목록에 보이고, breadcrumb 에서 안쪽 경로가 사라진다.
      await expect(page.getByRole("row", { name: new RegExp(outer) })).toBeVisible();
      await expect(page.getByRole("button", { name: inner })).toHaveCount(0);
    } finally {
      await purgeFolder(page, outer);
    }
  });

  test("다른 메뉴를 거치지 않고 같은 메뉴를 연달아 눌러도 루트로 돌아온다", async ({ page }) => {
    const outer = `e2e-nav2-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      await page.getByRole("button", { name: "새 폴더" }).click();
      await page.getByPlaceholder("폴더 이름").fill(outer);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();

      // 들어갔다 → 네비 → 다시 들어갔다 → 네비. 두 번 모두 루트로 와야 한다.
      for (let i = 0; i < 2; i += 1) {
        await page.getByRole("button", { name: outer }).click();
        await expect(page.getByText("이 폴더가 비어 있습니다")).toBeVisible();

        await page.getByRole("link", { name: "내 드라이브" }).click();
        await expect(page.getByRole("row", { name: new RegExp(outer) })).toBeVisible();
      }
    } finally {
      await purgeFolder(page, outer);
    }
  });
});
