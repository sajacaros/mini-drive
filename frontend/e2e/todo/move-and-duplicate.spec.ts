// spec: 할 일을 다른 날짜로 옮기고 복제한다. 루틴 항목을 미뤄도 원래 날짜에 다시 생기지 않고,
//       옮겨 간 날에는 그날치와 두 건으로 나란히 선다(backend/app/services/todos.py 의 origin_date).
// seed: frontend/e2e/seed.spec.ts

import { test, expect, type Page } from "@playwright/test";
import { loginAsAdmin } from "../support/auth";
import { existsNow } from "../support/locators";

/** 서비스 기준 '오늘'은 KST 고정이라(backend/app/services/todos.py) 같은 시간대로 날짜를 뽑는다. */
function todayKst(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
}

function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d + n);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "2026-08-10" → "8월 10일 (월)" — 화면의 '옮김' 배지와 같은 표기. */
function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const weekday = ["월", "화", "수", "목", "금", "토", "일"][
    (new Date(y, m - 1, d).getDay() + 6) % 7
  ];
  return `${m}월 ${d}일 (${weekday})`;
}

/** 제목으로 그날 목록의 행을 집는다. */
const row = (page: Page, title: string) => page.locator("li[data-todo-id]", { hasText: title });

/** 모달을 열어 날짜를 채우고 확정한다. */
async function pickDate(page: Page, date: string, confirm: "옮기기" | "복제") {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("날짜").fill(date);
  await dialog.getByRole("button", { name: confirm, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

/** 그날 목록에서 제목이 맞는 항목을 모두 지운다(루틴 파생 항목은 삭제 버튼이 없어 건너뛴다). */
async function cleanUp(page: Page, date: string, titles: string[]) {
  await page.goto(`/todo?date=${date}`);
  for (const title of titles) {
    const items = row(page, title);
    if (!(await existsNow(items.first(), 3_000))) continue;
    for (let left = await items.count(); left > 0; left--) {
      const del = items.first().getByRole("button", { name: "삭제" });
      if (!(await existsNow(del, 1_000))) break; // 루틴 파생 — 루틴을 지워야 사라진다
      await del.click();
      await expect(items).toHaveCount(left - 1);
    }
  }
}

test.describe("할 일 옮기기·복제", () => {
  test("임시 항목을 옮기면 원래 날짜에서 사라지고 옮긴 날에 배지와 함께 나타난다", async ({
    page,
  }) => {
    const today = todayKst();
    const tomorrow = addDays(today, 1);
    const title = `e2e-move-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto(`/todo?date=${today}`);
      await page.getByPlaceholder("할 일을 추가하세요").fill(title);
      await page.getByRole("button", { name: "추가" }).click();
      await expect(row(page, title)).toBeVisible();

      await row(page, title).getByRole("button", { name: "다른 날짜로 옮기기" }).click();
      await pickDate(page, tomorrow, "옮기기");

      // expect: 오늘 목록에서 즉시 사라진다
      await expect(row(page, title)).toHaveCount(0);
      // 서버에 남은 결과다 — 새로고침해도 오늘에는 없다.
      await page.reload();
      await expect(row(page, title)).toHaveCount(0);

      // expect: 내일 목록에 "N월 N일에서 옮김" 배지와 함께 있다
      await page.goto(`/todo?date=${tomorrow}`);
      await expect(row(page, title)).toBeVisible();
      await expect(row(page, title)).toContainText(`${dayLabel(today)}에서 옮김`);
    } finally {
      await cleanUp(page, tomorrow, [title]);
      await cleanUp(page, today, [title]);
    }
  });

  test("복제하면 원본이 남은 채 고른 날짜에 사본이 생긴다", async ({ page }) => {
    const today = todayKst();
    const tomorrow = addDays(today, 1);
    const title = `e2e-dup-${Date.now()}`;

    try {
      await loginAsAdmin(page);
      await page.goto(`/todo?date=${today}`);
      await page.getByPlaceholder("할 일을 추가하세요").fill(title);
      await page.getByRole("button", { name: "추가" }).click();
      await expect(row(page, title)).toBeVisible();

      // 같은 날 복제 — 목록이 즉시 두 줄이 된다.
      await row(page, title).first().getByRole("button", { name: "복제" }).click();
      await pickDate(page, today, "복제");
      await expect(row(page, title)).toHaveCount(2);

      // 다른 날 복제 — 오늘은 그대로 두 줄, 내일에 한 줄이 생긴다.
      await row(page, title).first().getByRole("button", { name: "복제" }).click();
      await pickDate(page, tomorrow, "복제");
      await expect(row(page, title)).toHaveCount(2);

      await page.goto(`/todo?date=${tomorrow}`);
      const copy = row(page, title);
      await expect(copy).toHaveCount(1);
      // 사본은 그날 태어난 항목이라 '옮김' 배지가 없다.
      await expect(copy).not.toContainText("에서 옮김");
    } finally {
      await cleanUp(page, tomorrow, [title]);
      await cleanUp(page, today, [title]);
    }
  });

  test("루틴 항목을 미뤄도 원래 날짜에 다시 채워지지 않는다", async ({ page }) => {
    const today = todayKst();
    const tomorrow = addDays(today, 1);
    const title = `e2e-routine-move-${Date.now()}`;

    try {
      await loginAsAdmin(page);

      // 매일 루틴을 만들면 오늘·내일 각각 그날치가 물질화된다.
      await page.goto("/routines");
      await page.getByRole("button", { name: "루틴 추가" }).click();
      await page.getByPlaceholder("예: 매일 운동 30분").fill(title);
      await page.getByRole("button", { name: "저장" }).click();
      await expect(page.getByText("루틴을 만들었습니다.")).toBeVisible();

      await page.goto(`/todo?date=${today}`);
      await expect(row(page, title)).toHaveCount(1);

      await row(page, title).getByRole("button", { name: "다른 날짜로 옮기기" }).click();
      await pickDate(page, tomorrow, "옮기기");

      // expect: 오늘 자리는 비고, 새로고침해도 루틴이 그 자리를 다시 채우지 않는다.
      //         (origin_date 가 오늘로 남아 물질화가 "이미 만들었다"고 판정한다.)
      await expect(row(page, title)).toHaveCount(0);
      await page.reload();
      await expect(row(page, title)).toHaveCount(0);

      // expect: 내일에는 미뤄온 오늘치가 루틴 배지와 '옮김' 배지를 함께 달고 있다.
      //   내일치(b)는 여기서 보이지 않는다 — 물질화는 미래 날짜를 미리 만들지 않으므로
      //   내일이 실제로 와야 생긴다. 그때 origin_date 가 오늘인 이 항목은 내일치의 자리를
      //   차지하지 않으므로 b 가 따로 만들어져 두 건이 나란히 선다.
      await page.goto(`/todo?date=${tomorrow}`);
      const moved = row(page, title);
      await expect(moved).toHaveCount(1);
      await expect(moved).toContainText("루틴");
      await expect(moved).toContainText(`${dayLabel(today)}에서 옮김`);
    } finally {
      // 루틴을 지우면 파생 항목은 삭제 버튼이 생기는 임시 항목이 된다.
      await page.goto("/routines");
      const card = page.locator("li", { hasText: title });
      if (await existsNow(card)) {
        page.once("dialog", (d) => void d.accept());
        await card.getByRole("button", { name: "삭제" }).click();
        await expect(card).toHaveCount(0);
      }
      await cleanUp(page, tomorrow, [title]);
      await cleanUp(page, today, [title]);
    }
  });
});
