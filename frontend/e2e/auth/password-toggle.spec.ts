// spec: 비밀번호 칸은 눈 아이콘으로 보기/숨기기를 토글한다(PasswordInput).
// 가려진 채로는 왜 틀렸는지 알 수 없어서 넣었다 — 셋업 화면에서 확인 칸 불일치를 반복한 사례.

import { test, expect } from "@playwright/test";

// 로그인 화면을 봐야 하므로 저장된 인증 상태를 쓰지 않는다.
test.use({ storageState: { cookies: [], origins: [] } });

test("로그인 비밀번호를 눈 아이콘으로 보였다 숨긴다", async ({ page }) => {
  await page.goto("/login");
  const input = page.getByLabel("비밀번호", { exact: true });
  await input.fill("Secret!123");
  await expect(input).toHaveAttribute("type", "password");

  await page.getByRole("button", { name: "비밀번호 보기" }).click();
  await expect(input).toHaveAttribute("type", "text");
  await expect(input).toHaveValue("Secret!123");
  // 토글 버튼이 폼을 제출하면 안 된다(type="button").
  await expect(page).toHaveURL(/\/login$/);

  await page.getByRole("button", { name: "비밀번호 숨기기" }).click();
  await expect(input).toHaveAttribute("type", "password");
  await expect(input).toHaveValue("Secret!123");
});
