// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";

test.describe("실시간 목록 갱신 (SSE)", () => {
  test("새 폴더 생성 시 목록이 즉시 갱신된다", async ({ page }) => {
    const folderName = `e2e-sse-folder-${Date.now()}`;

    await loginAsAdmin(page);
    await page.goto("/");

    // 1. 드라이브 홈에서 '새 폴더' 버튼 클릭 → 다이얼로그의 '폴더 이름' textbox에 고유한 이름 입력 → '만들기' 클릭
    await page.getByRole("button", { name: "새 폴더" }).click();
    await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
    await page.getByRole("textbox", { name: "폴더 이름" }).fill(folderName);
    await page.getByRole("button", { name: "만들기" }).click();

    // expect: 다이얼로그가 닫히고 토스트 "폴더를 만들었습니다."가 보인다
    await expect(page.getByRole("heading", { name: "새 폴더" })).not.toBeVisible();
    await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();

    // expect: 재조회/새로고침 없이 테이블에 해당 폴더 행이 즉시 나타난다(권한 관리/이름 변경/삭제 액션 포함)
    const folderRow = page.getByRole("row", { name: new RegExp(folderName) });
    await expect(folderRow).toBeVisible();
    await expect(folderRow.getByRole("button", { name: "권한 관리" })).toBeVisible();
    await expect(folderRow.getByRole("button", { name: "이름 변경" })).toBeVisible();
    await expect(folderRow.getByRole("button", { name: "삭제" })).toBeVisible();

    // 2. 생성한 폴더를 삭제(휴지통 이동) 후 휴지통에서 영구 삭제하여 정리
    await folderRow.getByRole("button", { name: "삭제" }).click();
    await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
    await expect(
      page.getByText(`${folderName} 폴더와 하위 항목을 휴지통으로 이동하시겠습니까?`),
    ).toBeVisible();
    await page.getByRole("button", { name: "휴지통으로 이동" }).click();
    await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();

    // expect: 드라이브 홈 목록에서 폴더가 사라진다
    await expect(folderRow).not.toBeVisible();

    await page.goto("/trash");
    const trashRow = page.getByRole("row", { name: new RegExp(folderName) });
    await trashRow.getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
    await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
    await expect(trashRow).not.toBeVisible();
  });
});
