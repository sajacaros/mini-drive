/**
 * 화면 폭 질의 훅.
 *
 * "좁은 화면"과 "터치 기기"는 서로 다른 질문이라 훅도 나눠 둔다. 폭은 **무엇을 그릴지**를
 * 정하고(표 ↔ 카드, 사이드바 ↔ 서랍), 입력 방식은 **어떻게 반응할지**를 정한다(한 번 탭으로
 * 열기 — `useCoarsePointer`, lib/rowOpen.ts). 태블릿은 넓으면서 터치고, 데스크톱 터치스크린도
 * 있다. 둘을 한 플래그로 합치면 그런 기기에서 반드시 한쪽이 틀린다.
 *
 * 기준점은 Tailwind 의 md(768px)와 맞춘다 — CSS 쪽 `md:` 유틸과 JS 분기가 같은 지점에서
 * 갈려야 레이아웃이 어긋난 채로 겹치는 구간이 생기지 않는다.
 *
 * 구현은 useSyncExternalStore 다. matchMedia 는 React 밖에서 값이 바뀌는 외부 저장소라
 * 이쪽이 정확하다 — useState + useEffect 로 흉내 내면 첫 렌더와 구독 사이의 변화를 놓치지
 * 않으려고 effect 안에서 setState 를 한 번 더 해야 하고, 그게 곧 불필요한 재렌더다.
 */

import { useSyncExternalStore } from "react";

/** Tailwind md 미만. `md:` 유틸이 켜지는 지점(768px) 바로 아래까지. */
const NARROW = "(max-width: 767.98px)";

/** MediaQueryList 는 질의당 하나만 만들어 재사용한다(구독/해제가 같은 객체를 향하도록). */
const cache = new Map<string, MediaQueryList>();

function listFor(query: string): MediaQueryList {
  let mq = cache.get(query);
  if (!mq) {
    mq = window.matchMedia(query);
    cache.set(query, mq);
  }
  return mq;
}

/** 미디어 쿼리 구독 — 일치 여부를 돌려주고 변화에 따라 갱신한다. */
function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mq = listFor(query);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    () => listFor(query).matches,
  );
}

/** 휴대폰 폭(Tailwind md 미만)인지. 표 ↔ 카드처럼 **구조가 다른** 전환에 쓴다. */
export function useIsNarrow(): boolean {
  return useMediaQuery(NARROW);
}
