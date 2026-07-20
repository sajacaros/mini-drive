// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow, recentStrip } from "../support/locators";

test.describe("최근 이용 콘텐츠", () => {
  test("[엣지] 삭제된 파일은 최근 뷰/스트립에서 제외된다", async ({ page }) => {
    const fileName = `e2e-recent-hides-deleted-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e recent hides-deleted test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드하고 미리보기하여 최근 항목으로 등록한다
      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      const fileChooser = await fileChooserPromise;
      await fileChooser.setFiles(filePath);

      const row = page.getByRole("row", { name: namePattern });
      await expect(row).toBeVisible({ timeout: 20_000 });
      await row.getByRole("button", { name: "미리보기" }).click();
      await expect(page.getByRole("heading", { name: fileName })).toBeVisible();
      await page.getByRole("button", { name: "닫기" }).click();

      // 최근 항목 기록은 드라이브 홈 재방문 시 반영된다
      await page.goto("/");
      // expect: 드라이브 홈 "최근 항목" 스트립과 /recent 목록에 파일이 보인다
      // 스트립의 카드 버튼과 파일 목록 행의 버튼이 접근성 이름이 같으므로 스트립으로 범위를 좁힌다
      await expect(page.getByRole("heading", { name: "최근 항목" })).toBeVisible();
      await expect(recentStrip(page).getByRole("button", { name: fileName })).toBeVisible();

      await page.goto("/recent");
      await expect(page.getByRole("row", { name: namePattern })).toBeVisible();

      // 2. 드라이브 홈에서 해당 파일을 삭제(휴지통 이동)
      await page.goto("/");
      const homeRow = page.getByRole("row", { name: namePattern });
      await homeRow.getByLabel("삭제").click();
      await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
      await page.getByRole("button", { name: "휴지통으로 이동" }).click();

      // expect: 삭제 토스트가 보인다
      await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();

      // 3. 드라이브 홈과 /recent 를 각각 확인
      // expect: "최근 항목" 스트립이 사라지거나 해당 카드가 제거된다(유일한 항목이면 스트립 자체가 렌더되지 않음)
      await expect(page.getByRole("heading", { name: "최근 항목" })).not.toBeVisible();

      await page.goto("/recent");
      // expect: /recent 목록에서도 해당 파일이 사라진다(빈 상태라면 빈 상태 문구가 보인다)
      await expect(page.getByRole("row", { name: namePattern })).not.toBeVisible();
      await expect(page.getByText("최근 이용한 항목이 없습니다")).toBeVisible();
    } finally {
      // 4. /trash 에서 파일을 '영구 삭제'로 완전히 제거
      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: namePattern });
      if (await existsNow(trashRow)) {
        await trashRow.getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
        await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
        // expect: 드라이브가 정리된다
        await expect(trashRow).not.toBeVisible();
      }

      // 드라이브 홈에 아직 남아있을 경우(중간 단계 실패 대비) 최종 정리
      await page.goto("/");
      const row = page.getByRole("row", { name: namePattern });
      if (await existsNow(row)) {
        await row.getByLabel("삭제").click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
        await expect(row).not.toBeVisible();

        await page.goto("/trash");
        const leftoverTrashRow = page.getByRole("row", { name: namePattern });
        if (await existsNow(leftoverTrashRow)) {
          await leftoverTrashRow.getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
          await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
          await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
          await expect(leftoverTrashRow).not.toBeVisible();
        }
      }

      fs.rmSync(filePath, { force: true });
    }
  });
});
