// spec: spec/wiki-index.md 「프런트」 — 문서 카탈로그는 "위키가 무엇을, 어떻게 알고 있는가"를
//       보여준다. 목록에서 문서를 누르면 그 문서의 절 트리가 열리고, 절을 누르면 원문이 열린다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

// 절은 모두 짧게 둔다 — 400자 미만인 절은 본문이 그대로 요약이 되어(wiki_tree.SHORT_NODE_CHARS)
// LLM 호출 없이 색인이 끝난다. 사내 vLLM 응답 시간에 테스트가 매이지 않게 하려는 것이다.
const MD = `# 배포 가이드

배포 절차를 정리한다.

## 사전 준비

빌드 아티팩트와 마이그레이션을 확인한다.

## 롤백

직전 태그로 되돌린다.
`;

test.describe("문서 카탈로그", () => {
  test("위키를 켠 문서가 목록에 오르고, 눌러 절 트리를 본다", async ({ page }) => {
    const stamp = Date.now();
    const fileName = `e2e-wiki-${stamp}.md`;
    // 폴더 안에 올린다 — 카탈로그가 말하는 위치가 실제 경로인지 보려면 최상위로는 부족하다.
    const folderName = `e2e-위키폴더-${stamp}`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, MD);
    const namePattern = new RegExp(fileName.replace(".", "\\."));
    const folderPattern = new RegExp(folderName);

    try {
      await loginAsAdmin(page);
      await page.goto("/");

      await page.getByRole("button", { name: "새 폴더" }).click();
      await expect(page.getByRole("heading", { name: "새 폴더" })).toBeVisible();
      await page.getByRole("textbox", { name: "폴더 이름" }).fill(folderName);
      await page.getByRole("button", { name: "만들기" }).click();
      await expect(page.getByText("폴더를 만들었습니다.")).toBeVisible();
      await page
        .getByRole("row", { name: folderPattern })
        .getByRole("button", { name: folderName })
        .dblclick();

      const fileChooserPromise = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "업로드", exact: true }).click();
      (await fileChooserPromise).setFiles(filePath);
      await expect(page.getByText(`${fileName}: 업로드를 완료했습니다.`)).toBeVisible();

      // 위키 켜기 — 드라이브 행의 "위키 설정"이 유일한 입구다(카탈로그 화면에서는 못 켠다).
      const row = page.getByRole("row", { name: namePattern });
      await row.getByLabel("위키 설정").click();
      await expect(page.getByRole("heading", { name: `위키 설정 — ${fileName}` })).toBeVisible();
      // 토글은 서버 응답을 받은 뒤에야 켜진 상태가 된다(낙관적 갱신을 하지 않는다) —
      // check() 는 클릭 직후의 상태를 보므로 여기서는 클릭 + 상태 단언으로 나눈다.
      // 스위치는 **하나**다 — 켜면 색인과 전사 공개가 함께 걸린다(spec 「왜 스위치가 하나인가」).
      const indexToggle = page.getByRole("checkbox", { name: /전사 위키에 올리기/ });
      await indexToggle.click();
      await expect(indexToggle).toBeChecked();
      await page.getByRole("button", { name: "닫기" }).click();

      // 카탈로그 목록 — 색인 전(대기·진행)에도 보여야 한다. 워커가 끝내면 SSE 로 배지가 바뀐다.
      // 전사 위키는 수백 건이라 이름순 첫 페이지에 방금 켠 문서가 없다 — 검색으로 좁힌다.
      await page.goto("/wiki/catalog");
      await page.getByLabel("문서명으로 찾기").fill(fileName);
      const catalogRow = page.getByRole("row", { name: namePattern });
      await expect(catalogRow).toBeVisible();
      await expect(catalogRow.getByText("위키 포함", { exact: false })).toBeVisible({
        timeout: 60_000,
      });
      // 위치 — 이름이 같은 문서가 폴더마다 있어서, 이게 없으면 검색 결과에서 어느 것이 내가
      // 방금 켠 문서인지 가릴 수 없다. (소유자 표시명은 seed 에 매이므로 백엔드에서 검증한다.)
      await expect(catalogRow.getByText(folderName)).toBeVisible();

      // 문서를 누르면 그 문서의 카탈로그. 질의가 절을 고를 때 보는 것과 같은 트리다.
      await catalogRow.getByRole("button", { name: fileName }).click();
      // 목록의 검색어(`?q=`)를 함께 넘긴다 — 브레드크럼으로 돌아올 때 필터가 유지되어야 한다.
      await expect(page).toHaveURL(/\/wiki\/catalog\/\d+(\?|$)/);
      await expect(page.getByRole("heading", { name: fileName })).toBeVisible();
      // 상세도 같은 위치를 말한다 — 링크로 바로 들어온 사람에게는 여기가 유일한 단서다.
      await expect(page.getByText(folderName, { exact: false })).toBeVisible();
      for (const title of ["배포 가이드", "사전 준비", "롤백"]) {
        await expect(page.getByRole("button", { name: new RegExp(title) })).toBeVisible();
      }

      // 절을 누르면 원문 미리보기 — 답변의 근거 클릭과 같은 경로다.
      await page.getByRole("button", { name: /사전 준비/ }).click();
      await expect(page.getByRole("heading", { name: `${fileName} — 사전 준비` })).toBeVisible();
      await page.getByRole("button", { name: "닫기" }).click();

      // 브레드크럼으로 목록에 돌아온다 — 검색어가 유지되므로 방금 보던 문서가 그대로 보인다.
      // 필터가 풀리면 수백 건짜리 이름순 1페이지가 나오고, 문서가 사라진 것처럼 읽힌다.
      await page.getByRole("button", { name: "문서 카탈로그" }).click();
      await expect(page).toHaveURL(/\/wiki\/catalog(\?|$)/);
      await expect(catalogRow).toBeVisible();
    } finally {
      // 정리 — 휴지통 이동 후 영구 삭제(파일이 사라지면 카탈로그에서도 빠진다).
      // 폴더째 지운다 — 안의 문서도 함께 사라지므로 파일을 따로 지울 필요가 없다.
      await page.goto("/");
      const row = page.getByRole("row", { name: folderPattern });
      if (await existsNow(row)) {
        await row.getByLabel("삭제").click();
        await expect(page.getByRole("heading", { name: "휴지통으로 이동" })).toBeVisible();
        await page.getByRole("button", { name: "휴지통으로 이동" }).click();
        await expect(page.getByText("휴지통으로 이동했습니다.")).toBeVisible();
      }

      await page.goto("/trash");
      const trashRow = page.getByRole("row", { name: folderPattern });
      if (await existsNow(trashRow)) {
        await trashRow.getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByRole("heading", { name: "영구 삭제" })).toBeVisible();
        await page.getByRole("dialog").getByRole("button", { name: "영구 삭제" }).click();
        await expect(page.getByText("영구 삭제했습니다.")).toBeVisible();
      }

      fs.rmSync(filePath, { force: true });
    }
  });
});
