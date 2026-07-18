import axios, {
  AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";

import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/lib/tokenStore";
import type { TokenResponse } from "./types";

/*
 * 게이트웨이(nginx) 경유 시 프론트와 API 는 동일 오리진이므로 baseURL 은 /api.
 * 인터셉터: (1) access 토큰 자동 첨부, (2) 401 시 refresh 회전 후 원 요청 재시도.
 * refresh 는 회전(rotation)이므로 동시 401 이 여러 개 떠도 refresh 는 한 번만 수행하고,
 * 나머지 요청은 큐에 대기시켰다가 새 토큰으로 재시도한다.
 */
export const apiClient: AxiosInstance = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

/** refresh 토큰으로 새 토큰 쌍을 받는다. 인터셉터 루프를 피하려고 순수 axios 로 호출한다. */
async function requestRefresh(refreshToken: string): Promise<TokenResponse> {
  const { data } = await axios.post<TokenResponse>("/api/auth/refresh", {
    refresh_token: refreshToken,
  });
  return data;
}

/** refresh 실패(재로그인 필요) 시 호출할 콜백. auth store 가 등록한다. */
let onAuthFailure: (() => void) | null = null;
export function setAuthFailureHandler(handler: () => void): void {
  onAuthFailure = handler;
}

// 진행 중인 refresh 를 공유하기 위한 단일 프라미스. null 이면 아무도 갱신 중이 아님.
let refreshInFlight: Promise<string> | null = null;

/** 필요 시 refresh 를 한 번만 실행하고, 동시 호출은 같은 프라미스를 공유한다. */
function refreshAccessToken(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;

  const refreshToken = getRefreshToken();
  if (!refreshToken) return Promise.reject(new Error("no refresh token"));

  refreshInFlight = requestRefresh(refreshToken)
    .then((tokens) => {
      // 회전: 응답의 새 refresh 토큰으로 교체 저장 (필수).
      setTokens(tokens.access_token, tokens.refresh_token);
      return tokens.access_token;
    })
    .finally(() => {
      refreshInFlight = null;
    });

  return refreshInFlight;
}

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retried?: boolean })
      | undefined;

    const url = original?.url ?? "";
    // 인증 엔드포인트의 401/403 은 그대로 화면에 전달한다 (로그인 실패, refresh 실패 등).
    const isAuthEndpoint =
      url.includes("/auth/login") ||
      url.includes("/auth/refresh") ||
      url.includes("/auth/register");

    if (
      error.response?.status === 401 &&
      original &&
      !original._retried &&
      !isAuthEndpoint
    ) {
      original._retried = true;
      try {
        const newToken = await refreshAccessToken();
        original.headers.Authorization = `Bearer ${newToken}`;
        return apiClient(original);
      } catch {
        // 회전 실패 → 세션 종료 후 로그인으로.
        clearTokens();
        onAuthFailure?.();
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  },
);

/** 에러 응답에서 사용자에게 보여줄 메시지(detail)를 추출한다. */
export function extractErrorMessage(error: unknown, fallback = "요청을 처리하지 못했습니다."): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  }
  return fallback;
}

/** 에러의 HTTP 상태 코드(없으면 undefined). */
export function errorStatus(error: unknown): number | undefined {
  if (axios.isAxiosError(error)) return error.response?.status;
  return undefined;
}

export default apiClient;
