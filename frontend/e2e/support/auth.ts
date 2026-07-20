import { expect, type Page } from "@playwright/test";

/**
 * E2E 인증 공용 모듈 — test() 부작용 없는 순수 헬퍼.
 * `auth.setup.ts`(storageState 저장)와 개별 스펙(seed.spec.ts 경유)이 함께 쓴다.
 *
 * 자격 증명은 env 로 주입한다(기본값은 CLI 로 만든 e2e 테스트 admin):
 *   E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD
 */
export const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "e2e-admin@example.com";
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "E2eTest!pass9";

/** 폼 로그인 한 번 수행(auth.setup.ts 전용). 성공 시 사이드바 "내 드라이브"가 보인다. */
export async function loginViaForm(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("이메일").fill(ADMIN_EMAIL);
  await page.getByLabel("비밀번호").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page.getByRole("link", { name: "내 드라이브" })).toBeVisible({
    timeout: 15_000,
  });
}

/**
 * 인증 상태로 만들어 드라이브 홈(/)에 둔다 — **멱등**.
 * storageState 로 이미 로그인돼 있으면(전역 setup) 폼 로그인을 생략한다. 이렇게 해야
 * 매 테스트가 /login 을 다시 때리지 않아 로그인 rate limit(IP 당 5회/분)에 걸리지 않는다.
 * storageState 없이 단독 실행하는 경우에만 폼 로그인으로 폴백한다.
 *
 * "이미 로그인됨" 판정 타임아웃은 넉넉하게 잡아야 한다 — 스택이 바쁠 때(예: 전체 스위트를
 * 연속 실행) 첫 렌더가 몇 초 걸릴 수 있는데, 여기서 너무 일찍 포기하면 실제로는 유효한
 * 세션인데도 불필요한 폼 로그인을 시도하게 되고, 그런 "가짜 재로그인"이 여러 테스트에서
 * 겹치면 로그인 rate limit(5회/분)에 걸려 연쇄적으로 실패한다.
 */
export async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto("/");
  const driveLink = page.getByRole("link", { name: "내 드라이브" });
  const alreadyIn = await driveLink
    .waitFor({ state: "visible", timeout: 15_000 })
    .then(() => true)
    .catch(() => false);
  if (alreadyIn) return;
  await loginViaForm(page);
}
