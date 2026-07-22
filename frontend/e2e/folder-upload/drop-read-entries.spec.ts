// spec: spec/folder-upload-batch.md
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * 드래그 앤 드롭 경로 — FileSystem API 재귀 순회 검증.
 *
 * 노리는 함정: `FileSystemDirectoryReader.readEntries()` 는 한 번에 최대 100개만 돌려준다.
 * 한 번만 호출하면 101번째부터 **조용히 사라진다** — 에러도 없이 파일이 누락되는,
 * 눈으로는 잡기 어려운 종류의 버그다.
 *
 * 실제 드롭은 Playwright 로 만들 수 없다(브라우저가 OS 드래그에서만 FileSystemEntry 를
 * 만든다). 그래서 브라우저 동작을 그대로 흉내 낸 가짜 엔트리 트리를 합성 drop 이벤트로
 * 주입한다 — reader 가 100개씩 끊어 주고 마지막에 빈 배열을 주는 것까지 동일하다.
 * 앱 코드(entriesFromDrop → collectFromEntries → readAllEntries)는 진짜 그대로 돈다.
 */

const FILE_COUNT = 120; // 100 경계를 넘겨 두 번째 readEntries 호출을 강제한다.

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

test.describe("폴더 업로드 — 드래그 앤 드롭", () => {
  test("100개를 넘는 폴더도 누락 없이 전부 수집한다", async ({ page }) => {
    const rootName = `e2e-drop-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      // 가짜 FileSystemEntry 트리를 만들어 drop 이벤트로 주입한다.
      await page.getByTestId("dropzone").evaluate(
        (el, { name, count }) => {
          const files = Array.from(
            { length: count },
            (_, i) => new File([`content-${i}`], `f${String(i).padStart(3, "0")}.txt`),
          );

          const fileEntry = (file: File) => ({
            isFile: true,
            isDirectory: false,
            name: file.name,
            file: (cb: (f: File) => void) => cb(file),
          });

          // 브라우저와 동일하게 100개씩 끊어 주고, 다 떨어지면 빈 배열을 준다.
          const dirEntry = (dirName: string, children: unknown[]) => ({
            isFile: false,
            isDirectory: true,
            name: dirName,
            createReader() {
              let cursor = 0;
              return {
                readEntries(cb: (entries: unknown[]) => void) {
                  const batch = children.slice(cursor, cursor + 100);
                  cursor += batch.length;
                  cb(batch);
                },
              };
            },
          });

          const root = dirEntry(name, files.map(fileEntry));
          const dataTransfer = {
            // types 는 앱이 "페이지 안에서 끌어온 항목 이동"과 "바깥에서 온 업로드"를
            // 가르는 데 쓴다(FileBrowserPage 의 isInternalDrag). 실제 파일 드롭과 같이
            // ["Files"] 를 준다 — 빠뜨리면 드롭 핸들러가 그 자리에서 터진다.
            types: ["Files"],
            items: [{ kind: "file", webkitGetAsEntry: () => root }],
            files: [] as File[],
          };

          const event = new DragEvent("drop", { bubbles: true, cancelable: true });
          // dataTransfer 는 프로토타입의 읽기 전용 접근자라 인스턴스 속성으로 가린다.
          Object.defineProperty(event, "dataTransfer", { value: dataTransfer });
          el.dispatchEvent(event);
        },
        { name: rootName, count: FILE_COUNT },
      );

      // 사전 검사 다이얼로그에 전부(120개) 집계돼야 한다.
      // readEntries 를 한 번만 불렀다면 여기서 100개로 잡혀 실패한다.
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: "폴더 업로드" })).toBeVisible();
      await expect(dialog.getByText(new RegExp(`파일 ${FILE_COUNT}개`))).toBeVisible();

      await dialog.getByRole("button", { name: `${FILE_COUNT}개 업로드` }).click();
      await expect(page.getByText(`${FILE_COUNT}개 파일을 업로드했습니다.`)).toBeVisible({
        timeout: 60_000,
      });

      // 서버에도 전부 저장됐는지 확인한다. 목록은 50개씩 끊기므로 총 페이지 수로 총량을 본다
      // — 120개면 3페이지, 100개만 왔다면 2페이지라 여기서 갈린다.
      await page.getByRole("button", { name: rootName }).dblclick();
      await expect(page.getByRole("button", { name: "f000.txt" })).toBeVisible();
      await expect(page.getByText(`1 / ${Math.ceil(FILE_COUNT / 50)}`)).toBeVisible();

      // 마지막 페이지로 넘어가 경계 너머의 파일이 실제로 있는지 본다.
      await page.getByRole("button", { name: "다음" }).click();
      await page.getByRole("button", { name: "다음" }).click();
      await expect(
        page.getByRole("button", { name: `f${String(FILE_COUNT - 1).padStart(3, "0")}.txt` }),
      ).toBeVisible();
    } finally {
      await purgeFolder(page, rootName);
    }
  });
});
