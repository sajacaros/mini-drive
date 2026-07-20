/** 일반 사용자 조회 + 자기 정보 수정 API (그룹 초대 UX, 프로필). */

import apiClient from "./client";
import type { User, UserLookup } from "./types";

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

/**
 * 이름/이메일 부분 일치 active 사용자 검색 (그룹 초대 클릭 선택 UX용).
 * 최소 2자, 결과 상한 20, 자기 자신 제외. 과도 호출 시 429(Retry-After).
 * 이메일 열거 방지를 위해 결과 수가 제한된다.
 */
export async function searchUsers(query: string): Promise<UserLookup[]> {
  const { data } = await apiClient.get<UserLookup[]>("/users/search", {
    params: { q: query },
  });
  return data;
}

/** 현재 사용자의 표시 이름 수정 (프로필). 수정된 사용자 정보를 반환한다. */
export async function updateMe(displayName: string): Promise<User> {
  const { data } = await apiClient.patch<User>("/users/me", {
    display_name: displayName,
  });
  return data;
}
