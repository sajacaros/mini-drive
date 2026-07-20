/**
 * 페이지 헤더 공용 래퍼 — 좌측에 페이지가 넘긴 헤더 콘텐츠(제목/브레드크럼/툴바/필터 탭 등),
 * 맨 오른쪽 같은 라인에 프로필 칩을 배치한다. 각 페이지는 기존 border-b 헤더 블록과 padding 을
 * 그대로 소유하고, 그 안의 콘텐츠만 이 래퍼로 감싼다. 이렇게 하면 전용 헤더 줄 없이 프로필 칩이
 * 페이지 첫 줄 우측에 자연스럽게 붙는다.
 *
 * children 은 페이지가 원래 쓰던 그대로 넘기면 된다:
 *  - 제목 + 부제(블록 요소들) → 세로로 쌓이고 칩은 우상단.
 *  - 브레드크럼 + 툴바(justify-between 한 줄) → 그 줄 오른쪽 끝에 칩.
 */

import type { ReactNode } from "react";

import { ProfileMenu } from "./ProfileMenu";

export function PageHeader({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-4">
      <div className="min-w-0 flex-1">{children}</div>
      <div className="shrink-0 pt-0.5">
        <ProfileMenu />
      </div>
    </div>
  );
}
