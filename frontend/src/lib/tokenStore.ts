/**
 * 토큰 저장소 — localStorage 를 진실 소스로 삼는 얇은 래퍼.
 *
 * axios 인터셉터(client.ts)와 blob 다운로드(fetch)가 같은 access 토큰을 읽어야 하므로,
 * zustand store 가 아니라 이 모듈이 토큰 접근의 단일 관문이 된다. zustand auth store 는
 * 여기에 위임한다.
 */

const ACCESS_KEY = "minidrive.access_token";
const REFRESH_KEY = "minidrive.refresh_token";

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_KEY, accessToken);
  localStorage.setItem(REFRESH_KEY, refreshToken);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}
