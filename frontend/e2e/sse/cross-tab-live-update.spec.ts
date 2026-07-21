// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

test.describe("실시간 목록 갱신 (SSE)", () => {
  test("[핵심] 같은 세션의 두 번째 탭에 SSE로 변경이 자동 반영된다 (수동 새로고침 없음)", async ({
    page,
    context,
  }) => {
    const folderName = `e2e-sse-crosstab-${Date.now()}`;
    const namePattern = new RegExp(folderName);

    // 1. 탭 A: loginAsAdmin으로 로그인 후 드라이브 홈(/)에 머무른다.
    //    탭 B: 같은 admin 계정으로 별도 Page 를 열어 동일하게 로그인 후 같은 드라이브 홈(/)에 머무른다
    const pageA = page;
    await loginAsAdmin(pageA);
    await pageA.goto("/");

    const pageB = await context.newPage();

    try {
      await loginAsAdmin(pageB);
      await pageB.goto("/");

      // expect: 두 탭 모두 같은 폴더(루트) 목록을 보고 있다
      await expect(pageA.getByRole("navigation").getByRole("button", { name: "내 드라이브" })).toBeVisible();
      await expect(pageB.getByRole("navigation").getByRole("button", { name: "내 드라이브" })).toBeVisible();

      // 2. 탭 B에서 '새 폴더'를 만든다
      await pageB.getByRole("button", { name: "새 폴더" }).click();
      await expect(pageB.getByRole("heading", { name: "새 폴더" })).toBeVisible();
      await pageB.getByRole("textbox", { name: "폴더 이름" }).fill(folderName);
      await pageB.getByRole("button", { name: "만들기" }).click();
      await expect(pageB.getByText("폴더를 만들었습니다.")).toBeVisible();

      // expect: 탭 B 목록에 즉시 폴더가 나타난다
      const folderRowB = pageB.getByRole("row", { name: namePattern });
      await expect(folderRowB).toBeVisible();

      // 3. 탭 A에서 아무 것도 조작하지 않고(새로고침/재방문 없이) 수 초 대기하며 목록을 관찰한다
      // expect: 탭 A의 목록에도 새 폴더 행이 자동으로 나타난다 (GET /api/files/events SSE 반영)
      const folderRowA = pageA.getByRole("row", { name: namePattern });
      await expect(folderRowA).toBeVisible({ timeout: 10_000 });

      // 4. 탭 B에서 방금 만든 폴더를 삭제(휴지통 이동)하고 휴지통에서 영구 삭제
      await folderRowB.getByRole("button", { name: "삭제" }).click();
      await expect(pageB.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
      await pageB.getByRole("button", { name: "휴지통으로 이동" }).click();
      await expect(pageB.getByText("휴지통으로 이동했습니다.")).toBeVisible();
      await expect(folderRowB).not.toBeVisible();

      await pageB.goto("/trash");
      const trashRow = pageB.getByRole("row", { name: namePattern });
      await trashRow.getByRole("button", { name: "영구 삭제" }).click();
      await expect(pageB.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
      await pageB.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
      await expect(pageB.getByText("영구 삭제했습니다.")).toBeVisible();
      await expect(trashRow).not.toBeVisible();

      // expect: 탭 A에서도 별도 조작 없이 목록에서 폴더가 사라진다
      await expect(folderRowA).not.toBeVisible({ timeout: 10_000 });

      // expect: 두 탭 모두 해당 폴더가 사라진 상태로 복귀한다.
      // 내 드라이브 루트에는 "공유" 가상 폴더 행이 항상 고정 노출돼 빈 상태 문구가 뜨지 않으므로
      // 전역 빈 상태 대신 대상 행의 부재로 확인한다.
      await pageB.goto("/");
      await expect(pageB.getByRole("row", { name: namePattern })).not.toBeVisible();
    } finally {
      // 중간 단언이 실패해 happy-path 정리를 건너뛰었더라도 항상 정리한다(best-effort, 멱등).
      await pageB.goto("/");
      const folderRowB = pageB.getByRole("row", { name: namePattern });
      if (await existsNow(folderRowB)) {
        await folderRowB.getByRole("button", { name: "삭제" }).click();
        await expect(pageB.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await pageB.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(pageB.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        await expect(folderRowB).not.toBeVisible();
      }

      await pageB.goto("/trash");
      const trashRow = pageB.getByRole("row", { name: namePattern });
      if (await existsNow(trashRow)) {
        await trashRow.getByRole("button", { name: "영구 삭제" }).click();
        await expect(pageB.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
        await pageB.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
        await expect(pageB.getByText("영구 삭제했습니다.")).toBeVisible();
        await expect(trashRow).not.toBeVisible();
      }

      await pageB.close();
    }
  });
});
