/** 일반 사용자 조회 API — 그룹 초대 UX (PRD 6.7 users/lookup). */

import apiClient from "./client";
import type { UserLookup } from "./types";

/**
 * 이메일 정확 일치 active 사용자 조회. 없으면 404, 과도 호출 시 429(Retry-After).
 * 부분 검색은 이메일 열거 방지를 위해 지원하지 않는다.
 */
export async function lookupUserByEmail(email: string): Promise<UserLookup> {
  const { data } = await apiClient.get<UserLookup>("/users/lookup", {
    params: { email },
  });
  return data;
}
