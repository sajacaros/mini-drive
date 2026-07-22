// spec: spec/folder-upload-batch.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 사전 검사 — 규칙을 어긴 항목만 걸러내고 나머지는 그대로 올린다.
 *
 * 핵심은 "바이트 하나 보내기 전에" 판정한다는 것과, 위반 항목 하나 때문에 폴더 전체를
 * 막지 않는다는 것이다. 규칙은 서버 `normalize_relpath` 와 같아야 하며(fileTree.ts 주석),
 * 여기서 걸러도 서버 검증은 그대로 남는다.
 *
 * 실제 드롭을 만들 수 없어 가짜 FileSystemEntry 트리를 합성 drop 으로 주입한다
 * (drop-read-entries.spec.ts 와 같은 방식).
 */

/**
 * 깊이(32단계) 규칙은 여기서 다루지 않는다 — 깊이를 넘긴 파일을 넣으면 그 조상 폴더 중
 * 32단계 이내인 것들은 규칙을 통과해 **빈 폴더로 생성**되고(빈 폴더 보존은 의도된 동작),
 * 이 테스트의 초점인 "위반 항목만 건너뛴다"가 흐려진다. 깊이/경로 규칙 자체는 백엔드
 * 단위 테스트(tests/test_batch_upload_unit.py)가 규칙별로 덮는다.
 * 여기서는 파일 단위로 끝나는 이름 규칙 위반 두 건만 쓴다.
 */
const LONG_NAME = `${"n".repeat(256)}.txt`;

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

test.describe("폴더 업로드 — 사전 검사", () => {
  test("규칙 위반 항목만 건너뛰고 나머지는 업로드한다", async ({ page }) => {
    const rootName = `e2e-preflight-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 정상 1 + 제어문자 이름 1 + 255자 초과 이름 1 = 3개를 담은 트리를 주입한다.
      await page.getByTestId("dropzone").evaluate(
        (el, { name, longName }) => {
          const fileEntry = (file: File) => ({
            isFile: true,
            isDirectory: false,
            name: file.name,
            file: (cb: (f: File) => void) => cb(file),
          });

          const dirEntry = (dirName: string, children: unknown[]) => ({
            isFile: false,
            isDirectory: true,
            name: dirName,
            createReader() {
              let done = false;
              return {
                readEntries(cb: (entries: unknown[]) => void) {
                  cb(done ? [] : children);
                  done = true;
                },
              };
            },
          });

          const ok = fileEntry(new File(["fine"], "정상.txt"));
          // 아래 둘은 서버 normalize_relpath 와 같은 규칙으로 걸러진다.
          const control = fileEntry(new File(["bad"], "제어문자\x01.txt"));
          const tooLong = fileEntry(new File(["bad"], longName));

          const root = dirEntry(name, [ok, control, tooLong]);
          const dataTransfer = {
            // types 는 앱이 "페이지 안에서 끌어온 항목 이동"과 "바깥에서 온 업로드"를
            // 가르는 데 쓴다(FileBrowserPage 의 isInternalDrag). 실제 파일 드롭과 같이
            // ["Files"] 를 준다 — 빠뜨리면 드롭 핸들러가 그 자리에서 터진다.
            types: ["Files"],
            items: [{ kind: "file", webkitGetAsEntry: () => root }],
            files: [] as File[],
          };

          const event = new DragEvent("drop", { bubbles: true, cancelable: true });
          Object.defineProperty(event, "dataTransfer", { value: dataTransfer });
          el.dispatchEvent(event);
        },
        { name: rootName, longName: LONG_NAME },
      );

      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: "폴더 업로드" })).toBeVisible();

      // 위반 2건은 사유와 함께 "건너뜁니다" 목록에, 정상 1건만 업로드 대상이 된다.
      await expect(dialog.getByText("2개를 건너뜁니다")).toBeVisible();
      await expect(dialog.getByText(/이름에 쓸 수 없는 문자가 있습니다/)).toBeVisible();
      await expect(dialog.getByText(/이름이 255자를 넘습니다/)).toBeVisible();
      await expect(dialog.getByText(/파일 1개/)).toBeVisible();

      // 위반이 있어도 나머지는 그대로 진행된다.
      await dialog.getByRole("button", { name: "1개 업로드" }).click();

      // 건너뛴 항목은 실패로 함께 보고된다 — 개별 토스트가 아니라 집계로.
      await expect(page.getByText("1개 완료 · 2개 실패")).toBeVisible({ timeout: 30_000 });
      await expect(
        page.getByRole("heading", { name: "업로드하지 못한 항목" }),
      ).toBeVisible();
      // 모달 헤더의 ✕(aria-label="닫기")와 접근성 이름이 같으므로 본문 텍스트로 좁힌다.
      await page.getByRole("dialog").locator("button", { hasText: "닫기" }).click();

      // 정상 파일만 실제로 저장됐다 — 위반 항목은 전송 자체가 일어나지 않는다.
      await page.getByRole("button", { name: rootName }).dblclick();
      await expect(page.getByRole("button", { name: "정상.txt" })).toBeVisible();
      await expect(page.getByRole("row", { name: /제어문자/ })).toHaveCount(0);
    } finally {
      await purgeFolder(page, rootName);
    }
  });
});
