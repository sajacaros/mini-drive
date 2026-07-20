import { test as setup } from "@playwright/test";
import { loginViaForm } from "./support/auth";

/**
 * 전역 인증 setup — 스위트 전체에서 로그인을 **한 번만** 수행한다.
 * 폼 로그인 후 localStorage(minidrive.access_token/refresh_token)를 포함한 storageState 를
 * 저장하면, 이후 모든 테스트가 이 상태를 재사용해 /login 을 다시 때리지 않는다.
 * → 로그인 rate limit(IP 당 5회/분) 회피 + 실행 속도 향상.
 *
 * playwright.config.ts 의 "setup" 프로젝트가 이 파일을 실행하고, chromium 프로젝트가
 * dependencies 로 이걸 먼저 돌린 뒤 use.storageState 로 결과를 읽는다.
 */
const authFile = "e2e/.auth/admin.json";

setup("authenticate", async ({ page }) => {
  await loginViaForm(page);
  await page.context().storageState({ path: authFile });
});
