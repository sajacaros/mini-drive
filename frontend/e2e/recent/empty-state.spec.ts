// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test.describe("최근 이용 콘텐츠", () => {
  test("최근 빈 상태 확인 및 드라이브 홈에 스트립이 노출되지 않음", async ({ page }) => {
    // 1. loginAsAdmin으로 로그인. 최근 이용 기록이 없는 상태를 보장(필요 시 사전 정리)
    await loginAsAdmin(page);

    // 이전 실행에서 남은 "최근" 기록이 있다면 모두 정리해 recent 를 비운다(멱등 best-effort).
    await page.goto("/recent");
    let leftoverRow = page.locator("table tbody tr").first();
    while (await existsNow(leftoverRow)) {
      const leftoverName = await leftoverRow.getByRole("button").first().innerText();
      const namePattern = new RegExp(escapeRegExp(leftoverName));

      await page.goto("/");
      const homeRow = page.getByRole("row", { name: namePattern });
      if (await existsNow(homeRow)) {
        await homeRow.getByLabel("삭제").click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();

        await page.goto("/trash");
        const trashRow = page.getByRole("row", { name: namePattern });
        if (await existsNow(trashRow)) {
          await trashRow.getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
          await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
        }
      }

      await page.goto("/recent");
      leftoverRow = page.locator("table tbody tr").first();
    }

    // expect: /recent 이동 시 heading "최근"과 부제 "최근에 미리보거나 내려받은 파일입니다."가 보인다
    await expect(page.getByRole("heading", { name: "최근", exact: true })).toBeVisible();
    await expect(page.getByText("최근에 미리보거나 내려받은 파일입니다.")).toBeVisible();
    // expect: 빈 상태 문구 "최근 이용한 항목이 없습니다" / "파일을 미리보거나 내려받으면 여기에 표시됩니다."가 보인다
    await expect(page.getByText("최근 이용한 항목이 없습니다")).toBeVisible();
    await expect(page.getByText("파일을 미리보거나 내려받으면 여기에 표시됩니다.")).toBeVisible();

    // 2. 드라이브 홈(/)으로 이동
    await page.goto("/");

    // expect: heading "최근 항목" 스트립이 렌더되지 않는다(최근 기록이 없으므로)
    await expect(page.getByRole("heading", { name: "최근 항목" })).not.toBeVisible();
  });
});
