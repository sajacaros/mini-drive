// spec: spec/wiki-index.md + backend/app/services/chat/ — 대화형 질의는 근거를 달아 답하고,
//       맥락이 이어져 주어가 빠진 후속 질문이 통한다. 견주는 질문에는 표로 답한다.
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Locator, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/**
 * **실제 사내 vLLM 을 호출한다.** 백엔드 축은 `backend/tests/integration_chat.py` 가 이미 보므로
 * 여기서는 화면이 그 결과를 제대로 그리는지만 본다 — 근거 버튼이 미리보기를 열고, 비교 아티팩트가
 * 표가 되고, 검색 질의가 드러나는가.
 *
 * 검색 대상은 전사 위키 **전체**라 사내 문서 수백 건과 섞인다. 그래서 답이 내 문서에서 나왔음을
 * 확정할 수 있도록 다른 문서에 없을 코드명(ZEPHYR)을 쓰고, 근거의 파일명까지 확인한다.
 *
 * 절은 모두 400자 미만으로 둔다 — 짧은 절은 본문이 그대로 요약이 되어(wiki_tree.SHORT_NODE_CHARS)
 * 색인 단계에서 LLM 을 부르지 않는다. 색인 대기를 모델 응답 시간에 매지 않으려는 것이다.
 */
const MD = `# ZEPHYR 릴리스 절차

ZEPHYR 는 사내 배포 파이프라인의 코드명이다.

## 승인 권한

ZEPHYR 릴리스는 플랫폼 리드가 승인한다. 승인 없이 배포하면 즉시 롤백한다.

## 롤백 기준

ZEPHYR 배포 후 오류율이 2% 를 넘으면 롤백한다. 롤백은 직전 태그로 되돌린다.

## 공지 대상

ZEPHYR 릴리스는 전사 채널에 공지한다. 실패한 릴리스는 회고에 남긴다.
`;

const FIRST_QUESTION = "ZEPHYR 릴리스는 누가 승인하나요?";

/**
 * 색인 + 모델 왕복 3회가 한 시나리오에 들어간다. 실측(Solar-Open2)으로 질문 한 건이
 * 3~15초고 색인 대기가 최대 60초라 기본 60초로는 모자란다.
 */
test.describe.configure({ timeout: 300_000 });

/** 좌측 세션 목록. 사이드바(주 메뉴)도 같은 role 이라 "지난 대화" 제목으로 가른다. */
function sessionList(page: Page): Locator {
  return page.locator("aside").filter({ has: page.getByRole("heading", { name: "지난 대화" }) });
}

/** 대화 영역의 답변 카드들. 좌측 목록과 미리보기 모달은 이 안에 들어오지 않는다. */
function answers(page: Page): Locator {
  return page.locator("section").locator("div.card");
}

/**
 * 이 시나리오가 남긴 대화를 모두 지운다(멱등).
 *
 * **시작할 때도 부른다.** 실행이 답을 기다리다 죽으면 그때는 제목이 아직 "제목 없음"이라
 * 끝 정리가 못 잡는데, 뒤늦게 서버가 제목을 붙여 다음 실행에서 같은 제목이 둘이 된다
 * (strict mode 위반으로 엉뚱한 자리에서 깨진다 — 실측).
 */
async function purgeSessions(page: Page, title: string): Promise<void> {
  await page.goto("/chat");
  /*
    목록이 다 뜨기 전에 세면 0건으로 보여 정리를 조용히 건너뛴다. "로딩 표시가 없다"로 판정하면
    안 된다 — 아직 **뜨지도 않은** 순간에도 없기 때문이다(support/locators.ts 의 existsNow 주석과
    같은 함정). 로딩이 끝나야만 나타나는 것, 즉 빈 상태 문구나 행 하나를 기다린다.
  */
  const rows = sessionList(page).locator("div.group").filter({ hasText: title });
  await expect(
    sessionList(page)
      .getByText("아직 대화가 없습니다.")
      .or(sessionList(page).locator("div.group").first()),
  ).toBeVisible();

  let remaining = await rows.count();
  while (remaining > 0) {
    await rows.first().hover();
    await rows.first().getByRole("button", { name: "대화 삭제" }).click();
    await expect(rows).toHaveCount(remaining - 1);
    remaining -= 1;
  }
}

/** 질문을 보낸다 — 답을 기다리지 않는다(기다리는 **동안**의 화면을 보려면 이쪽을 쓴다). */
async function send(page: Page, question: string): Promise<void> {
  await page.getByPlaceholder("예: 2026년 계획을 작년과 비교해 주세요").fill(question);
  await page.getByRole("button", { name: "보내기" }).click();
  // 질문은 낙관적으로 먼저 그려진다 — 답까지 수십 초가 걸리는 동안 입력이 먹었는지
  // 화면에 없으면 사용자가 다시 누른다(ChatPage 의 모듈 주석).
  await expect(page.getByText(question, { exact: true }).first()).toBeVisible();
}

/** 답이 도착할 때까지 기다린다. 진행 표시가 사라지면 답이 붙었거나 오류가 났다. */
async function awaitAnswer(page: Page): Promise<void> {
  await expect(page.getByText("문서를 찾아 답을 만들고 있습니다…")).toHaveCount(0, {
    timeout: 180_000,
  });
}

async function ask(page: Page, question: string): Promise<void> {
  await send(page, question);
  await awaitAnswer(page);
}

test.describe("대화형 위키 질의", () => {
  test("근거를 달아 답하고, 주어가 빠진 후속 질문이 통하며, 견주면 표가 된다", async ({
    page,
  }) => {
    const stamp = Date.now();
    const fileName = `e2e-chat-${stamp}.md`;
    const folderName = `e2e-채팅폴더-${stamp}`;
    const filePath = path.join(os.tmpdir(), fileName);
    fs.writeFileSync(filePath, MD);
    const namePattern = new RegExp(fileName.replace(".", "\\."));
    const folderPattern = new RegExp(folderName);

    try {
      await loginAsAdmin(page);
      // 앞선 실행이 남긴 같은 제목의 대화를 먼저 치운다 — 둘이면 목록 단언이 모호해진다.
      await purgeSessions(page, FIRST_QUESTION);
      await page.goto("/");

      // ── 준비: 문서를 올리고 위키를 켠 뒤 색인이 끝날 때까지 기다린다 ──
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

      const row = page.getByRole("row", { name: namePattern });
      await row.getByLabel("위키 설정").click();
      await expect(page.getByRole("heading", { name: `위키 설정 — ${fileName}` })).toBeVisible();
      // 토글은 서버 응답을 받은 뒤에야 켜진다(낙관적 갱신을 하지 않는다).
      const indexToggle = page.getByRole("checkbox", { name: /전사 위키에 올리기/ });
      await indexToggle.click();
      await expect(indexToggle).toBeChecked();
      await page.getByRole("button", { name: "닫기" }).click();

      // 색인이 끝나야 검색 대상이 된다 — 카탈로그의 "위키 포함" 배지가 그 신호다.
      await page.goto("/wiki/catalog");
      await page.getByLabel("문서명으로 찾기").fill(fileName);
      const catalogRow = page.getByRole("row", { name: namePattern });
      await expect(catalogRow.getByText("위키 포함", { exact: false })).toBeVisible({
        timeout: 60_000,
      });

      // ── 1. 첫 질문 — 답에 근거가 붙는다 ──
      await page.goto("/chat");
      await page.getByRole("button", { name: "새 채팅" }).click();
      await expect(page.getByText("무엇이든 물어보세요")).toBeVisible();

      await send(page, FIRST_QUESTION);

      /*
        **답을 기다리는 동안**에도 목록이 무엇을 물었는지 말한다. 답까지 수십 초가 걸리는데
        그 사이 왼쪽이 "제목 없음"이면 화면에 도는 것은 진행 표시뿐이라, 질문이 들어갔는지
        목록만 보고는 알 수 없다. 제목 규칙은 서버와 같아야 한다 — 어긋나면 답이 도착하는
        순간 글자가 바뀌어 잘못 그렸다가 고친 것처럼 보인다(ChatPage 의 deriveTitle).
      */
      // 2초는 **답변 경로로는 닿을 수 없는** 시간이다(실측 최속 3.4초). 낙관적 제목은 네트워크
      // 없이 state 만 바꾸므로 즉시다 — 이 제한이 없으면 "답이 온 뒤 목록이 갱신됐다"도 통과해
      // 버려, 고치기 전 동작을 그대로 눈감아 준다.
      await expect(
        sessionList(page).locator("div.group").filter({ hasText: FIRST_QUESTION }),
      ).toBeVisible({ timeout: 2_000 });

      await awaitAnswer(page);

      const answer = answers(page).last();
      await expect(answer).toContainText("플랫폼 리드");

      // 모델이 검색을 실제로 불렀는가 — 툴 콜링이 꺼져 있으면 이 줄이 안 생긴다.
      // 답이 엉뚱할 때 원인은 대개 "검색이 다른 걸 가져왔다"이고 최종 답변만 봐서는 안 보인다.
      const traces = page.getByText(/^검색: /);
      await expect(traces.last()).toBeVisible();

      // ── 2. 근거는 접힌 채로 오고, 펴면 원문 미리보기로 이어진다 ──
      // 접힌 채로도 **몇 건인지는** 말한다 — 근거가 붙었다는 사실은 답을 믿을지 정하는 데
      // 필요하고, 그건 펴 보기 전에 알아야 한다.
      const citationToggle = answer.getByRole("button", { name: /^근거 \d+건$/ });
      await expect(citationToggle).toBeVisible();
      await expect(citationToggle).toHaveAttribute("aria-expanded", "false");
      // 접혀 있으면 목록 자체가 없다(감춰만 두는 것이 아니다).
      await expect(answer.getByRole("button", { name: namePattern })).toHaveCount(0);

      await citationToggle.click();
      await expect(citationToggle).toHaveAttribute("aria-expanded", "true");

      // 근거가 **내 문서**를 가리킨다. 전사 위키 전체가 검색 대상이라 이 확인이 없으면
      // 엉뚱한 문서를 근거로 든 답도 통과한다.
      const citation = answer.getByRole("button", { name: namePattern }).first();
      await expect(citation).toBeVisible();
      // 앵커는 페이지가 아니라 줄 번호다(md 트리의 좌표가 line_num).
      await expect(citation).toContainText("줄");

      await citation.click();
      const preview = page.getByRole("dialog");
      // 제목은 "파일명 — 절 제목" 이다. 어느 절이 뽑히든 내 문서의 한 절이어야 한다.
      await expect(
        preview.getByRole("heading", { name: new RegExp(`^${fileName.replace(".", "\\.")} — .+`) }),
      ).toBeVisible();
      await expect(preview).toContainText("ZEPHYR");
      await preview.getByRole("button", { name: "닫기" }).click();
      await expect(preview).toHaveCount(0);

      // ── 3. 후속 질문 — 주어가 없다 ──
      // "그럼 롤백은?" 에는 ZEPHYR 가 없다. 서버가 앞선 턴을 모델에 넘기고 모델이 그것을
      // **독립형 검색 질의**로 바꿔야 검색이 헛돌지 않는다. 이 축이 단발 질의와의 차이다.
      await ask(page, "그럼 롤백은 언제 하나요?");

      const followUp = answers(page).last();
      await expect(followUp).toContainText(/오류율|2\s?%/);
      // 맥락이 이어졌다는 것은 검색 질의에 드러난다 — 질문에 없던 주제가 질의에 들어간다.
      await expect(traces.last()).toContainText(/ZEPHYR|릴리스/);

      // ── 4. 견주는 질문 → 비교표 아티팩트 ──
      // 답변의 형태는 앞단에서 분류하지 않는다. 모델이 검색 결과를 본 뒤 렌더 툴을 고르고,
      // 화면은 kind 로 렌더러만 고른다(ArtifactView). 표로 그려지는지는 여기서만 보인다.
      await ask(page, "ZEPHYR 의 승인·롤백·공지 기준을 표로 비교해 주세요.");

      // Markdown 렌더러는 표를 만들지 않으므로(components/Markdown.tsx — 제목·리스트·문단만),
      // 이 화면의 <table> 은 comparison 아티팩트에서만 나온다. 평문으로 흘러내리면 여기서 갈린다.
      const table = page.getByRole("table").last();
      await expect(table).toBeVisible();
      expect(
        await table.getByRole("columnheader").count(),
        "비교표는 최소 두 열이어야 견줄 수 있다",
      ).toBeGreaterThanOrEqual(2);
      expect(await table.locator("tbody tr").count()).toBeGreaterThanOrEqual(1);
      /*
        **행 수는 못 박지 않는다.** 견줄 대상을 열에 놓을지 행에 놓을지는 모델이 정한다 —
        실측에서 같은 질문에 3행×2열도, 1행×3열도 나온다. 표의 형태 규약(직사각형·열 수 일치)은
        backend/tests/integration_chat.py 가 보고, 여기서 지킬 것은 "질문한 대상이 표에 담겼다"다.
      */
      await expect(table).toContainText("승인");
      await expect(table).toContainText("롤백");

      // ── 5. 대화가 목록에 남는다 — 첫 질문이 제목이 된다 ──
      const sessionRow = sessionList(page).locator("div.group").filter({ hasText: FIRST_QUESTION });
      await expect(sessionRow).toBeVisible();
      await page.reload();
      await expect(
        sessionList(page).locator("div.group").filter({ hasText: FIRST_QUESTION }),
      ).toBeVisible();
    } finally {
      // 대화 정리 — 남겨 두면 다음 실행의 목록 맨 앞을 차지한다.
      await purgeSessions(page, FIRST_QUESTION);

      // 문서 정리 — 폴더째 지우면 안의 문서도 함께 사라져 카탈로그·검색 대상에서 빠진다.
      await page.goto("/");
      const folderRow = page.getByRole("row", { name: folderPattern });
      if (await existsNow(folderRow)) {
        await folderRow.getByLabel("삭제").click();
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
