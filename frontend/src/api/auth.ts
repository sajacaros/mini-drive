/** 인증 API (PRD 6.1). */

import apiClient from "./client";
import type { RegisterResponse, TokenResponse, User } from "./types";

export interface RegisterPayload {
  email: string;
  password: string;
  display_name: string;
  /** 관리자 발급 가입 코드 (필수, PRD 3.1). */
  signup_code: string;
}

export async function register(payload: RegisterPayload): Promise<RegisterResponse> {
  const { data } = await apiClient.post<RegisterResponse>("/auth/register", payload);
  return data;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>("/auth/login", { email, password });
  return data;
}

export async function fetchMe(): Promise<User> {
  const { data } = await apiClient.get<User>("/auth/me");
  return data;
}

export async function logout(refreshToken: string): Promise<void> {
  await apiClient.post("/auth/logout", { refresh_token: refreshToken });
}
