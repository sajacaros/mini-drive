/** 가입 코드 관리 API (admin 전용, PRD 6.7). */

import apiClient from "./client";
import type {
  SignupCode,
  SignupCodeCreateRequest,
  SignupCodeListResponse,
  SignupCodeUpdateRequest,
} from "./types";

export async function listSignupCodes(page = 1, size = 20): Promise<SignupCodeListResponse> {
  const { data } = await apiClient.get<SignupCodeListResponse>("/admin/signup-codes", {
    params: { page, size },
  });
  return data;
}

export async function createSignupCode(payload: SignupCodeCreateRequest): Promise<SignupCode> {
  const { data } = await apiClient.post<SignupCode>("/admin/signup-codes", payload);
  return data;
}

/**
 * 가입 코드 수정 (부분 갱신). payload 에 담긴 키만 반영된다.
 * expires_at/max_uses 에 명시적 null 을 담으면 무기한/무제한으로 초기화된다.
 */
export async function updateSignupCode(
  id: number,
  payload: SignupCodeUpdateRequest,
): Promise<SignupCode> {
  const { data } = await apiClient.patch<SignupCode>(`/admin/signup-codes/${id}`, payload);
  return data;
}
