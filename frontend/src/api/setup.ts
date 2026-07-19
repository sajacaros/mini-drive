/** 첫 부팅 셋업 위저드 API (PRD 3.6.2, 6.1). 무인증 엔드포인트. */

import apiClient from "./client";
import type { SetupRequest, SetupResponse, SetupStatusResponse } from "./types";

/** 셋업 필요 여부 조회 (무인증) — admin 0명이면 setup_required=true. */
export async function getSetupStatus(): Promise<SetupStatusResponse> {
  const { data } = await apiClient.get<SetupStatusResponse>("/setup/status");
  return data;
}

/** 첫 admin + 초기 가입 코드 + 기본 할당량 셋업. 이미 셋업됐으면 409. */
export async function performSetup(payload: SetupRequest): Promise<SetupResponse> {
  const { data } = await apiClient.post<SetupResponse>("/setup", payload);
  return data;
}
