/**
 * 목록 항목의 "선택 / 열기" 상호작용 — 구글 드라이브와 같은 규칙을 공용화한다.
 *
 * - 마우스: 한 번 클릭 = 선택(하이라이트만), 더블클릭 = 열기(폴더 이동 / 파일 미리보기)
 * - Ctrl/Cmd 클릭 = 선택 토글, Shift 클릭 = 범위 선택 (수식 키가 있으면 열지 않는다)
 * - 터치: 더블탭 관용이 없으므로 한 번 탭 = 바로 열기
 * - 키보드: 이름 버튼에서 Enter/Space 로 누른 click 은 detail=0 으로 올라오므로 바로 열기
 *
 * 행 안의 즐겨찾기 별·액션 아이콘처럼 "행을 고르는 뜻이 아닌" 영역은 ROW_ACTION_PROPS 를
 * 펼쳐 두면 선택/열기 판정에서 빠진다.
 */

import { useEffect, useState } from "react";
import type { MouseEvent } from "react";

const COARSE_POINTER = "(pointer: coarse)";

/** 더블클릭 대신 한 번 탭으로 열어야 하는 환경(터치)인지. */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => window.matchMedia(COARSE_POINTER).matches);
  useEffect(() => {
    const mq = window.matchMedia(COARSE_POINTER);
    const sync = () => setCoarse(mq.matches);
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return coarse;
}

/** 행 클릭 판정에서 제외할 영역에 펼쳐 넣는다. */
export const ROW_ACTION_PROPS = { "data-row-action": "" } as const;

function fromRowAction(e: MouseEvent): boolean {
  return e.target instanceof Element && e.target.closest("[data-row-action]") !== null;
}

/** 클릭에 실린 다중 선택 의도. 둘 다 false 면 "이 항목 하나만" 이라는 뜻이다. */
export interface SelectMods {
  /** Ctrl/Cmd — 선택에 더하거나 뺀다. */
  toggle: boolean;
  /** Shift — 직전 기준점부터 여기까지 범위 선택. */
  range: boolean;
}

const NO_MODS: SelectMods = { toggle: false, range: false };

/** 행/카드 컨테이너에 그대로 펼쳐 넣는 클릭 핸들러 묶음. */
export function rowOpenHandlers(o: {
  coarse: boolean;
  select: (mods: SelectMods) => void;
  open: () => void;
}) {
  return {
    onClick: (e: MouseEvent) => {
      if (fromRowAction(e)) return;
      const mods: SelectMods = { toggle: e.ctrlKey || e.metaKey, range: e.shiftKey };
      // 수식 키를 눌렀다면 뜻은 언제나 "선택" 이다 — 터치/키보드의 바로 열기보다 우선한다.
      if (mods.toggle || mods.range) {
        o.select(mods);
        return;
      }
      if (e.detail === 0 || o.coarse) o.open();
      else o.select(NO_MODS);
    },
    onDoubleClick: (e: MouseEvent) => {
      if (fromRowAction(e)) return;
      o.open();
    },
  };
}

/** 더블클릭 시 글자가 블록 선택되지 않도록 행에 함께 준다. */
export const ROW_BASE_CLASS = "cursor-pointer select-none";

/** 선택된 행(테이블) / 카드(그리드) 하이라이트. */
export const ROW_SELECTED_CLASS =
  "bg-[color:var(--bg-muted)] outline outline-1 -outline-offset-1 outline-[color:var(--accent)]";
export const CARD_SELECTED_CLASS = "bg-[color:var(--bg-muted)] ring-1 ring-[color:var(--accent)]";
