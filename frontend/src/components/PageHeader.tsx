/**
 * 페이지 헤더 공용 래퍼 — 좌측에 페이지가 넘긴 헤더 콘텐츠(제목/브레드크럼/툴바/필터 탭 등),
 * 맨 오른쪽 같은 라인에 프로필 칩을 배치한다. 각 페이지는 기존 border-b 헤더 블록과 padding 을
 * 그대로 소유하고, 그 안의 콘텐츠만 이 래퍼로 감싼다. 이렇게 하면 전용 헤더 줄 없이 프로필 칩이
 * 페이지 첫 줄 우측에 자연스럽게 붙는다.
 *
 * align:
 *  - "center"(기본) — 한 줄형(브레드크럼/툴바/버튼) 헤더. 칩이 그 줄과 수직 중앙 정렬돼
 *    좌측 버튼(높이 ~36px)과 중심이 맞는다.
 *  - "start" — 제목+부제/필터·탭처럼 세로로 여러 줄 쌓이는 헤더. 칩을 첫 줄(제목)에 맞춰
 *    상단 정렬한다.
 */

import type { ReactNode } from "react";

import { ProfileMenu } from "./ProfileMenu";

export function PageHeader({
  children,
  align = "center",
}: {
  children: ReactNode;
  align?: "center" | "start";
}) {
  return (
    <div className={`flex gap-4 ${align === "start" ? "items-start" : "items-center"}`}>
      <div className="min-w-0 flex-1">{children}</div>
      <div className="shrink-0">
        <ProfileMenu />
      </div>
    </div>
  );
}
