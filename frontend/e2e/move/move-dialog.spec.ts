// spec: spec/minidrive-prd.md 6.2 (POST /api/files/{id}/move)
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/** 목록에서 폴더를 만들고 이름을 돌려준다. */
async function makeFolder(page: import("@playwright/test").Page, name: string) {
  await page.getByRole("button", { name: "새 폴더" }).click();
  await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
  await page.getByRole("textbox", { name: "폴더 이름" }).fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
  await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
}

/** 토스트(role=status)만 골라낸다 — 같은 문구를 쓰는 다이얼로그와 섞이지 않게. */
function toast(page: import("@playwright/test").Page, pattern: RegExp) {
  return page.getByRole("status").filter({ hasText: pattern });
}

/** 휴지통까지 비워 흔적을 남기지 않는다. */
async function purge(page: import("@playwright/test").Page, pattern: RegExp) {
  await page.goto("/");
  const row = page.getByRole("row", { name: pattern });
  if (await existsNow(row)) {
    await row.getByLabel("삭제").click();
    await page.getByRole("button", { name: "휴지통으로 이동" }).click();
    await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
  }
  await page.goto("/trash");
  const trashRow = page.getByRole("row", { name: pattern });
  if (await existsNow(trashRow)) {
    await trashRow.getByRole("button", { name: "영구 삭제" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
  }
}

test.describe("이동", () => {
  test("이동 다이얼로그로 폴더를 골라 항목을 옮긴다", async ({ page }) => {
    const stamp = Date.now();
    const dstName = `e2e-move-dst-${stamp}`;
    const itemName = `e2e-move-item-${stamp}`;

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, dstName);
      await makeFolder(page, itemName);

      // 1. 옮길 항목 행의 "이동" 아이콘 → 선택기 열림
      const itemRow = page.getByRole("row", { name: new RegExp(itemName) });
      await itemRow.getByLabel("이동").click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: new RegExp(itemName) })).toBeVisible();

      // 이미 있는 위치(내 드라이브)로는 확정할 수 없다.
      await expect(dialog.getByRole("button", { name: /내 드라이브.*이동/ })).toBeDisabled();

      // 2. 목적지 폴더로 들어가면 확정 버튼이 그 폴더를 가리킨다
      await dialog.getByRole("button", { name: dstName }).click();
      // 확정 버튼 문구의 조사는 폴더 이름에 따라 로/으로/(으)로 로 갈린다 — 이름만 확인한다.
      const confirm = dialog.getByRole("button", { name: new RegExp(`${dstName}.*이동`) });
      await expect(confirm).toBeEnabled();
      await confirm.click();

      // expect: 토스트 + 현재 목록에서 사라짐
      // 토스트(role=status)로 좁힌다 — 그냥 getByText 로 잡으면 아직 닫히지 않은 이동
      // 다이얼로그(제목=항목 이름, 확정 버튼="…(으)로 이동")가 같은 문구로 매치돼
      // 이동이 실패해도 통과해 버린다.
      await expect(toast(page, new RegExp(`${itemName}.*${dstName}.*이동`))).toBeVisible();
      await expect(itemRow).not.toBeVisible();

      // 3. 목적지 폴더를 열면 그 안에 있다 (한 번 클릭은 선택, 열기는 더블클릭)
      await page.getByRole("row", { name: new RegExp(dstName) }).getByText(dstName).dblclick();
      await expect(page.getByRole("row", { name: new RegExp(itemName) })).toBeVisible();

      // 4. breadcrumb 으로 돌아오면 목록에 없다(이동이 실제로 반영됨)
      await page.getByRole("button", { name: "내 드라이브" }).click();
      await expect(page.getByRole("row", { name: new RegExp(itemName) })).not.toBeVisible();
    } finally {
      // 하위 항목부터 지워야 폴더 삭제가 남는 것 없이 끝난다.
      await page.goto("/");
      const dstRow = page.getByRole("row", { name: new RegExp(dstName) });
      if (await existsNow(dstRow)) {
        await dstRow.getByText(dstName).dblclick();
        const inner = page.getByRole("row", { name: new RegExp(itemName) });
        if (await existsNow(inner)) {
          await inner.getByLabel("삭제").click();
          await page.getByRole("button", { name: "휴지통으로 이동" }).click();
          await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        }
      }
      await purge(page, new RegExp(itemName));
      await purge(page, new RegExp(dstName));
    }
  });

  test("항목을 폴더 행 위로 끌어다 놓으면 이동한다", async ({ page }) => {
    const stamp = Date.now();
    const dstName = `e2e-drag-dst-${stamp}`;
    const itemName = `e2e-drag-item-${stamp}`;

    await loginAsAdmin(page);
    await page.goto("/");

    try {
      await makeFolder(page, dstName);
      await makeFolder(page, itemName);

      const itemRow = page.getByRole("row", { name: new RegExp(itemName) });
      const dstRow = page.getByRole("row", { name: new RegExp(dstName) });
      await expect(itemRow).toBeVisible();
      await expect(dstRow).toBeVisible();

      // 드래그 앤 드롭 — HTML5 DnD 이벤트를 Playwright 가 합성한다.
      await itemRow.dragTo(dstRow);

      await expect(toast(page, new RegExp(`${itemName}.*${dstName}.*이동`))).toBeVisible();
      await expect(itemRow).not.toBeVisible();

      // 목적지 안에 실제로 들어갔는지 확인 (한 번 클릭은 선택, 열기는 더블클릭)
      await dstRow.getByText(dstName).dblclick();
      await expect(page.getByRole("row", { name: new RegExp(itemName) })).toBeVisible();

      // breadcrumb 으로 다시 꺼내기 — 상위 경로가 드롭 대상이다
      const innerRow = page.getByRole("row", { name: new RegExp(itemName) });
      await innerRow.dragTo(page.getByRole("button", { name: "내 드라이브" }));
      await expect(toast(page, new RegExp(`${itemName}.*내 드라이브.*이동`))).toBeVisible();
      await expect(innerRow).not.toBeVisible();
    } finally {
      await purge(page, new RegExp(itemName));
      await purge(page, new RegExp(dstName));
    }
  });
});
