import type { Locator, Page } from "@playwright/test";

/**
 * 드라이브 홈 "최근 항목" 스트립 컨테이너.
 *
 * 스트립의 카드 버튼(title=파일명)과 파일 목록 테이블의 행 버튼(텍스트=파일명)은
 * 접근성 이름이 동일해 `page.getByRole("button", { name })`을 페이지 전체에 대고 쓰면
 * strict-mode 위반(2개 매치)이 난다. 스트립 컨테이너로 먼저 좁혀서 모호성을 없앤다.
 */
export function recentStrip(page: Page) {
  return page.getByRole("heading", { name: "최근 항목" }).locator("xpath=..");
}

/**
 * "지금 이 요소가 존재/보이는가"를 안전하게 판정한다.
 *
 * `locator.isVisible()`은 대기하지 않고 현재 DOM 스냅샷만 즉시 확인하므로, 직전에
 * `page.goto()`를 호출한 직후처럼 아직 데이터 로딩/렌더링이 끝나기 전이면 실제로는
 * 존재하는 항목도 거의 항상 false로 오판된다(정리 로직이 조용히 스킵되는 버그의 원인).
 * `waitFor({ state: "visible" })`로 렌더를 기다리고, 정말 없으면 타임아웃 후 false로
 * 폴백하는 이 헬퍼를 best-effort 정리(try/finally)의 조건 분기에 사용해야 한다.
 */
export async function existsNow(locator: Locator, timeout = 5_000): Promise<boolean> {
  return locator
    .waitFor({ state: "visible", timeout })
    .then(() => true)
    .catch(() => false);
}
