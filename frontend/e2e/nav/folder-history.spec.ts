// spec: 폴더가 주소에 남아 브라우저 뒤로가기는 "상위 폴더", 새로고침은 "같은 자리"를 뜻한다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 폴더 이동이 URL 에 남지 않던 시절, 세 단계를 파고든 뒤 뒤로가기를 누르면 상위 폴더가
 * 아니라 **앱 밖**(직전에 보던 다른 메뉴)으로 나갔다. 새로고침은 루트로 리셋됐고 폴더를
 * 링크로 건넬 방법도 없었다. 같은 앱 안에서 화면마다 뒤로가기의 뜻이 달랐던 셈이다.
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
  }

  await page.goto("/trash");
  const trashRow = page.getByRole("row", { name: namePattern });
  if (await existsNow(trashRow)) {
    await trashRow.getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
  }
}

async function makeFolder(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "새 폴더" }).click();
  await page.getByPlaceholder("폴더 이름").fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
  // 앞선 토스트가 아직 안 사라졌을 수 있다 — 개수가 아니라 "떴는가"만 본다.
  await expect(page.getByText("폴더를 만들었습니다.").first()).toBeVisible();
  await expect(page.getByRole("button", { name })).toBeVisible();
}

test.describe("폴더 히스토리", () => {
  test("뒤로가기는 한 단계 위로 가고, 새로고침은 그 폴더에 머문다", async ({ page }) => {
    const outer = `e2e-hist-${Date.now()}`;
    const middle = "가운데";
    const inner = "안쪽";

    try {
      await loginAsAdmin(page);
      await page.goto("/");
      await expect(page).toHaveURL(/\/$/);

      // 3단계로 파고든다. 목록에서 폴더를 여는 건 더블클릭이다(lib/rowOpen.ts).
      await makeFolder(page, outer);
      await page.getByRole("button", { name: outer }).dblclick();
      await expect(page).toHaveURL(/\/f\/\d+$/);
      const outerUrl = page.url();

      await makeFolder(page, middle);
      await page.getByRole("button", { name: middle }).dblclick();
      const middleUrl = page.url();
      expect(middleUrl).not.toBe(outerUrl);

      await makeFolder(page, inner);
      await page.getByRole("button", { name: inner }).dblclick();
      const innerUrl = page.url();
      await expect(page.getByText("이 폴더가 비어 있습니다")).toBeVisible();

      // 새로고침 — 루트로 리셋되지 않고 그 자리에 남는다. breadcrumb 도 다시 선다.
      await page.reload();
      await expect(page).toHaveURL(innerUrl);
      await expect(page.getByRole("button", { name: outer })).toBeVisible();
      await expect(page.getByRole("button", { name: middle })).toBeVisible();
      await expect(page.getByText("이 폴더가 비어 있습니다")).toBeVisible();

      // 뒤로가기 = 한 단계 위. 앱 밖으로 나가지 않는다.
      await page.goBack();
      await expect(page).toHaveURL(middleUrl);
      await expect(page.getByRole("row", { name: new RegExp(inner) })).toBeVisible();

      await page.goBack();
      await expect(page).toHaveURL(outerUrl);
      await expect(page.getByRole("row", { name: new RegExp(middle) })).toBeVisible();

      await page.goBack();
      await expect(page).toHaveURL(/\/$/);
      await expect(page.getByRole("row", { name: new RegExp(outer) })).toBeVisible();

      // 앞으로가기도 대칭이어야 한다.
      await page.goForward();
      await expect(page).toHaveURL(outerUrl);
      await expect(page.getByRole("row", { name: new RegExp(middle) })).toBeVisible();

      // breadcrumb 으로 올라가는 길도 그대로 — 최상위 crumb 을 누르면 루트다.
      await page.goForward();
      await page.goForward();
      await expect(page).toHaveURL(innerUrl);
      await page.getByRole("button", { name: "내 드라이브" }).click();
      await expect(page).toHaveURL(/\/$/);
      await expect(page.getByRole("row", { name: new RegExp(outer) })).toBeVisible();

      // 링크로 건네도 열린다 — 새 탭에서 중간 폴더 주소로 바로 진입.
      const fresh = await page.context().newPage();
      await fresh.goto(middleUrl);
      await expect(fresh.getByRole("row", { name: new RegExp(inner) })).toBeVisible();
      await expect(fresh.getByRole("button", { name: outer })).toBeVisible();
      await fresh.close();
    } finally {
      await purgeFolder(page, outer);
    }
  });

  test("열 수 없는 폴더 주소는 루트로 돌려보낸다", async ({ page }) => {
    await loginAsAdmin(page);

    // 없는 id — 에러 화면에 가두면 상위로 갈 crumb 조차 없다. 요청을 되쏘지도 않아야 한다.
    let breadcrumbCalls = 0;
    page.on("request", (r) => {
      if (r.url().includes("/breadcrumb")) breadcrumbCalls += 1;
    });

    await page.goto("/f/999999999");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("button", { name: "새 폴더" })).toBeVisible();

    await page.waitForTimeout(1000);
    expect(breadcrumbCalls, "경로 복원 요청이 되풀이되면 안 된다").toBeLessThanOrEqual(2);
  });
});
