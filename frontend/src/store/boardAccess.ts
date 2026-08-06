/**
 * 사이드 네비의 "게시판" 항목 노출 여부 store.
 *
 * spec/group-board.md: *접근 가능한 게시판이 없으면 항목 자체를 감춘다.* 서버가 애초에 접근
 * 가능한 것만 내려주므로 판정은 "목록이 비었는가" 한 줄이고, 프런트가 권한을 다시 계산하지
 * 않는다.
 *
 * 조회는 세션당 한 번이면 충분하다 — 그룹 할당은 관리자만 바꾸고 자주 바뀌지 않는다. 관리
 * 화면에서 방금 할당을 바꾼 경우를 위해 `invalidate` 로 다음 refresh 가 다시 읽게 한다.
 */

import { create } from "zustand";

import { listBoards } from "@/api/boards";

interface BoardAccessState {
  /** null = 아직 모른다(항목을 그리지 않는다). 조회 전에 깜빡이지 않게 하는 값이다. */
  hasBoards: boolean | null;
  refresh: () => Promise<void>;
  /** 다음 refresh 가 서버를 다시 읽게 한다 (게시판 관리 화면에서 할당을 바꾼 뒤). */
  invalidate: () => void;
}

let loaded = false;
let inFlight = false;

export const useBoardAccessStore = create<BoardAccessState>((set) => ({
  hasBoards: null,

  refresh: async () => {
    if (loaded || inFlight) return;
    inFlight = true;
    try {
      const { items } = await listBoards();
      set({ hasBoards: items.length > 0 });
      loaded = true;
    } catch {
      // 네트워크/일시 오류면 이전 값으로 둔다 — 메뉴 하나가 늦게 뜨는 것이 전부다.
    } finally {
      inFlight = false;
    }
  },

  invalidate: () => {
    loaded = false;
  },
}));
