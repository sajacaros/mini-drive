import { disableRateLimit } from "./support/stack";

/**
 * 스위트 시작 전 1회 — rate limiting 을 끈다(support/stack.ts 에 근거).
 *
 * `globalTeardown` 이 원복한다. 여기서 실패해도 스위트를 막지 않는다 — 최악의 결과가
 * "지금과 같음(업로드 몇 건 429)"이라, 실행 자체를 세우는 것보다 낫다.
 */
export default function globalSetup(): void {
  disableRateLimit();
}
