// spec: 휴대폰(390×844)에서 화면이 제 기능을 하는지 — 가로 넘침과 도달 불가 액션.
// seed: 이 파일 안에서 API 로 직접 올린다(support/shares.ts 와 같은 방식).

import { test, expect, type Page } from "@playwright/test";

/**
 * 데스크톱 크로미움으로는 절대 잡히지 않는 회귀를 겨냥한다.
 *
 *  - hover 로만 나타나던 행 액션: hover 가 없는 기기에서는 계산된 opacity 가 0 으로 굳어
 *    이름 변경·공유·삭제가 통째로 도달 불가였다. 데스크톱 프로젝트에서는 hover 가 늘
 *    가능하므로 이 상태를 재현할 수 없다.
 *  - 가로 넘침: 표가 컨테이너보다 세 배 넓어 이름 외에는 아무것도 보이지 않았다.
 */

/** 브라우저 안에서 토큰을 꺼내 API 로 파일을 올린다(UI 업로드보다 빠르고 흔들리지 않는다). */
async function uploadTextFile(page: Page, name: string, body: string): Promise<number> {
  return page.evaluate(
    async ({ name, body }) => {
      const token = localStorage.getItem("minidrive.access_token");
      const form = new FormData();
      form.append("file", new File([body], name, { type: "text/plain" }));
      const res = await fetch("/api/files/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!res.ok) throw new Error(`upload failed: ${res.status}`);
      return (await res.json()).id as number;
    },
    { name, body },
  );
}

async function purge(page: Page, fileId: number): Promise<void> {
  await page.evaluate(async (id) => {
    const token = localStorage.getItem("minidrive.access_token");
    const h = { Authorization: `Bearer ${token}` };
    await fetch(`/api/files/${id}/delete`, { method: "POST", headers: h });
    await fetch(`/api/files/${id}/permanent-delete`, { method: "POST", headers: h });
  }, fileId);
}

/** 문서 전체가 가로로 밀리는지. 0 이 아니면 화면 밖으로 나간 요소가 있다는 뜻이다. */
async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test.describe("모바일 레이아웃", () => {
  test("주요 화면이 가로로 넘치지 않는다", async ({ page }) => {
    const paths = [
      "/",
      "/favorites",
      "/recent",
      "/trash",
      "/shares",
      "/todo",
      "/todo/month",
      "/todo/reports",
      "/groups",
      "/admin/users",
      "/admin/audit",
    ];
    for (const path of paths) {
      await page.goto(path);
      /*
        networkidle 은 쓰지 않는다 — 이 앱은 /api/files/events 로 SSE 를 계속 열어 두므로
        네트워크가 영영 조용해지지 않고 그대로 타임아웃난다. 대신 모든 인증 화면의 헤더에
        있는 프로필 칩이 뜰 때까지 기다린다: 그게 곧 "레이아웃이 그려졌다"는 신호다.
      */
      await expect(page.getByRole("button", { name: "내 계정" })).toBeVisible({
        timeout: 20_000,
      });
      expect(await horizontalOverflow(page), `${path} 가 가로로 넘친다`).toBe(0);
    }
  });

  test("행 액션이 터치에서 보이고 눌린다", async ({ page }) => {
    const name = `e2e-mobile-actions-${Date.now()}.txt`;
    let fileId: number | null = null;
    await page.goto("/");
    try {
      fileId = await uploadTextFile(page, name, "mobile row action test");
      await page.reload();

      // expect: ⋯ 가 hover 없이 그냥 보인다(예전에는 opacity:0 으로 잠겨 있었다).
      const more = page.getByRole("button", { name: `${name} 작업 더 보기` });
      await expect(more).toBeVisible({ timeout: 20_000 });

      await more.click();
      const sheet = page.getByRole("dialog", { name: `${name} 작업` });
      await expect(sheet).toBeVisible();

      // expect: 라벨이 붙은 채로 주요 동작이 모두 들어 있다.
      for (const label of ["미리보기", "다운로드", "공유", "이름 변경", "삭제"]) {
        await expect(sheet.getByRole("button", { name: label })).toBeVisible();
      }

      // expect: 실제로 눌리고 대상 모달이 열린다(시트는 닫힌다).
      await sheet.getByRole("button", { name: "공유" }).click();
      await expect(sheet).not.toBeVisible();
      await expect(page.getByRole("heading", { name: /공유/ })).toBeVisible();
    } finally {
      if (fileId !== null) await purge(page, fileId);
    }
  });

  test("파일 목록이 카드로 크기·수정일까지 보여준다", async ({ page }) => {
    const name = `e2e-mobile-card-${Date.now()}.txt`;
    let fileId: number | null = null;
    await page.goto("/");
    try {
      fileId = await uploadTextFile(page, name, "x".repeat(2048));
      await page.reload();

      // expect: 표(가로 스크롤)가 아니라 카드다 — 이름 옆에 크기와 수정일이 같이 보인다.
      await expect(page.getByText(name)).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText(/2\.0 KB · /)).toBeVisible();
      expect(await horizontalOverflow(page)).toBe(0);
    } finally {
      if (fileId !== null) await purge(page, fileId);
    }
  });
});

test.describe("모바일 뷰어", () => {
  test("마크다운 공유 링크가 서식대로 열리고 원문으로 되돌릴 수 있다", async ({ page, browser }) => {
    const name = `e2e-mobile-doc-${Date.now()}.md`;
    const body = [
      "# 제목",
      "",
      "본문과 [링크](https://example.com) 입니다.",
      "",
      "| 항목 | 값 |",
      "| --- | --- |",
      "| 하나 | 1 |",
      "",
      "```bash",
      "echo hello",
      "```",
    ].join("\n");

    let fileId: number | null = null;
    await page.goto("/");
    try {
      fileId = await uploadTextFile(page, name, body);
      const shareUrl: string = await page.evaluate(async (id) => {
        const token = localStorage.getItem("minidrive.access_token");
        const res = await fetch("/api/shares", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({ file_id: id, permission: "read" }),
        });
        return (await res.json()).share_url as string;
      }, fileId);

      // 공유 링크는 무인증 경로다 — 저장된 로그인 상태를 물려받지 않은 창으로 연다.
      const anon = await browser.newContext();
      const guest = await anon.newPage();
      try {
        await guest.goto(`/s/${shareUrl}`);
        await guest.getByRole("button", { name: "미리보기" }).click();

        const doc = guest.locator(".markdown-doc");
        await expect(doc).toBeVisible({ timeout: 20_000 });
        // expect: 예전 최소 렌더러가 못 그리던 것들이 실제로 그려진다.
        await expect(doc.locator("h1")).toHaveText("제목");
        await expect(doc.locator("table")).toBeVisible();
        await expect(doc.locator("pre code")).toContainText("echo hello");
        await expect(doc.locator('a[target="_blank"]')).toHaveAttribute(
          "rel",
          /noopener/,
        );
        // expect: 읽기 전용 공유라 다운로드 동선은 없다.
        await expect(guest.getByRole("button", { name: "다운로드" })).toHaveCount(0);
        expect(await horizontalOverflow(guest)).toBe(0);

        // expect: 원문 토글이 서식을 걷어낸다.
        await guest.getByRole("button", { name: "원문 보기" }).click();
        await expect(doc).toHaveCount(0);
        await expect(guest.locator("pre")).toContainText("| 항목 | 값 |");
      } finally {
        await anon.close();
      }
    } finally {
      if (fileId !== null) await purge(page, fileId);
    }
  });

  test("확장자만으로도 미리보기 형식을 알아낸다", async ({ page }) => {
    // 브라우저·OS 에 확장자 매핑이 없으면 .md 가 application/octet-stream 으로 올라온다.
    // 그 상태로도 텍스트(마크다운)로 판정되어야 한다 — 예전에는 415 로 떨어졌다.
    const name = `e2e-mobile-octet-${Date.now()}.md`;
    let fileId: number | null = null;
    await page.goto("/");
    try {
      fileId = await page.evaluate(async (name) => {
        const token = localStorage.getItem("minidrive.access_token");
        const form = new FormData();
        // 일부러 "형식 모름"으로 올린다.
        form.append("file", new File(["# 확장자 판정"], name, { type: "application/octet-stream" }));
        const res = await fetch("/api/files/upload", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: form,
        });
        return (await res.json()).id as number;
      }, name);

      const res = await page.evaluate(async (id) => {
        const token = localStorage.getItem("minidrive.access_token");
        const r = await fetch(`/api/files/${id}/preview`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return { status: r.status, kind: r.headers.get("x-preview-text-kind") };
      }, fileId);

      expect(res.status).toBe(200);
      expect(res.kind).toBe("markdown");
    } finally {
      if (fileId !== null) await purge(page, fileId);
    }
  });
});
