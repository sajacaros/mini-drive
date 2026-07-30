import type { Page } from "@playwright/test";

/**
 * 저장 용량 검증 헬퍼 — **화면 텍스트가 아니라 API 값을 본다.**
 *
 * 사이드바의 "138 MB / 10.0 GB" 를 비교하면 표시가 세 자리로 반올림되기 때문에, 계정에 이미
 * 수백 MB 가 쌓인 상태에서 수십 바이트를 올려도 텍스트가 그대로다. 그러면 기능은 정상인데
 * 테스트만 실패한다 — 거의 빈 계정을 가정한 단언이었다.
 *
 * `users.storage_used` 는 바이트 정수라 1바이트 변화도 잡힌다. 사이드바가 그 값을 렌더한다는
 * 것은 다른 단언(행 추가·토스트)이 이미 덮으므로, 용량 축은 정확한 쪽으로 본다.
 *
 * 토큰은 로그인 상태의 localStorage 에서 읽는다 — auth.setup 이 저장한 storageState 와 같은
 * 세션이라 별도 로그인이 필요 없다(support/shares.ts 와 같은 방식).
 */

const TOKEN_KEY = "minidrive.access_token";

/** 현재 로그인 사용자의 사용 용량(바이트). */
export async function storageUsed(page: Page): Promise<number> {
  return page.evaluate(async (tokenKey) => {
    const res = await fetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${localStorage.getItem(tokenKey)}` },
    });
    if (!res.ok) throw new Error(`/api/auth/me 실패: ${res.status} ${await res.text()}`);
    return (await res.json()).storage_used as number;
  }, TOKEN_KEY);
}
