import { restoreRateLimit } from "./support/stack";

/**
 * 스위트 종료 후 1회 — rate limiting 을 원래 값으로 되돌린다.
 *
 * 테스트가 실패해도 돈다. 프로세스가 SIGKILL 되면 못 도는데, 그때는 `e2e/.auth/`
 * 의 상태 파일이 남아 다음 실행이 "이미 꺼져 있음"을 알아채고 원복 대상을 유지한다.
 */
export default function globalTeardown(): void {
  restoreRateLimit();
}
