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

/**
 * 현재 사용자의 비밀번호 변경. 성공 시 204(본문 없음). 서버는 성공 시 이 계정의 모든
 * refresh 세션을 폐기하므로 다른 기기에서는 재로그인이 필요하다.
 * 오류: 400(현재 비밀번호 불일치), 422(정책 위반), 429(rate limit) — detail 메시지 노출.
 */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await apiClient.put("/users/me/password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

/**
 * 아바타 이미지 업로드 (multipart `file`). 성공 200 → 새 avatar_url("...?v={epoch}").
 * 오류(서버 detail 노출): 415(비허용 타입), 413(2MB 초과), 422(빈 파일), 429(rate limit).
 * files.ts 의 파일 업로드와 동일하게 요청별 Content-Type 을 multipart/form-data 로 지정한다
 * (지정하지 않으면 apiClient 기본 헤더 application/json 이 남아 boundary 없이 전송됨).
 */
export async function uploadAvatar(file: Blob, filename: string): Promise<{ avatar_url: string }> {
  const form = new FormData();
  form.append("file", file, filename);
  const { data } = await apiClient.post<{ avatar_url: string }>("/users/me/avatar", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

/** 아바타 삭제 → 기본 아이콘으로. 204(멱등). */
export async function deleteAvatar(): Promise<void> {
  await apiClient.delete("/users/me/avatar");
}
