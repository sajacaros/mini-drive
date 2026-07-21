// spec: frontend/specs/drive-ux-phase8.plan.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow, recentStrip } from "../support/locators";

test.describe("실시간 목록 갱신 (SSE)", () => {
  test("삭제(휴지통 이동) 시 목록에서 즉시 사라지고 최근 항목에서도 제외된다", async ({ page }) => {
    const fileName = `e2e-sse-delete-${Date.now()}.txt`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, `e2e delete reflects test content ${Date.now()}`);
    const namePattern = new RegExp(fileName.replace(".", "\\."));

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 1. 테스트 파일을 업로드하고 미리보기 실행하여 최근 항목에 등록되게 함
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

      // expect: 드라이브 홈에 "최근 항목" 스트립이 파일과 함께 노출된다
      // 스트립의 카드 버튼과 파일 목록 행의 버튼이 접근성 이름이 같으므로 스트립으로 범위를 좁힌다
      await expect(page.getByRole("heading", { name: "최근 항목" })).toBeVisible();
      await expect(recentStrip(page).getByRole("button", { name: fileName })).toBeVisible();

      // 2. 해당 파일 행의 '삭제' 버튼 클릭 → 다이얼로그에서 '휴지통으로 이동' 클릭
      await row.getByRole("button", { name: "삭제" }).click();
      await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
      await expect(page.getByText(`${fileName} 항목을 휴지통으로 이동하시겠습니까?`)).toBeVisible();
      await page.getByRole("button", { name: "휴지통으로 이동" }).click();

      // expect: 토스트 "휴지통으로 이동했습니다."가 보인다
      await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
      // expect: 목록 테이블에서 해당 행이 즉시 제거된다
      // (내 드라이브 루트에는 "공유" 가상 폴더 행이 항상 고정 노출되므로 빈 상태 문구는 뜨지 않는다)
      await expect(row).not.toBeVisible();
      // expect: "최근 항목" 스트립 전체가 사라지거나 해당 카드가 제거된다
      await expect(page.getByRole("heading", { name: "최근 항목" })).not.toBeVisible();

      // 3. /recent 로 이동
      await page.goto("/recent");
      // expect: 삭제한 파일이 목록에 없다(전체가 비었다면 빈 상태 문구가 보인다)
      await expect(page.getByRole("button", { name: fileName })).not.toBeVisible();
      await expect(page.getByText("최근 이용한 항목이 없습니다")).toBeVisible();

      // 4. /trash 에서 해당 파일을 '영구 삭제'로 완전히 제거
      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: namePattern });
      await trashRow.getByRole("button", { name: "영구 삭제" }).click();
      await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
      await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
      await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
      // expect: 휴지통 목록에서 사라진다
      await expect(trashRow).not.toBeVisible();
    } finally {
      // 중간 단언이 실패해 happy-path 정리를 건너뛰었더라도 항상 드라이브를 정리한다(best-effort, 멱등).
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

      fs.rmSync(filePath, { force: true });
    }
  });
});
