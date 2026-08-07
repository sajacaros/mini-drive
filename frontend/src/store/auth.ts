/**
 * 인증 상태 store (Zustand).
 *
 * 토큰의 진실 소스는 localStorage(tokenStore)이고, store 는 그것을 감싼 뷰 + 현재 사용자
 * 정보(me)를 들고 있다. axios 인터셉터(client.ts)가 refresh 회전과 실패 시 로그아웃을
 * 처리하며, 여기서는 그 실패 콜백을 등록해 store 상태를 동기화한다.
 */

import { create } from "zustand";

import { fetchMe, login as apiLogin, logout as apiLogout } from "@/api/auth";
import { setAuthFailureHandler } from "@/api/client";
import { getSetupStatus } from "@/api/setup";
import type { User } from "@/api/types";
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "@/lib/tokenStore";

interface AuthState {
  user: User | null;
  /** 앱 부팅 시 기존 토큰으로 me 를 조회하는 초기화가 끝났는지. */
  initialized: boolean;
  isAuthenticated: boolean;
  /**
   * 첫 부팅 셋업 필요 여부 (admin 0명). 부팅 시 GET /api/setup/status 로 판정한다.
   * true 면 App 라우팅이 모든 경로를 /setup 으로 유도한다 (PRD 3.6.2).
   */
  setupRequired: boolean;
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  clearSession: () => void;
  /** 셋업 완료 후 재진입을 차단하기 위해 플래그를 내린다. */
  markSetupComplete: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  initialized: false,
  isAuthenticated: Boolean(getAccessToken()),
  setupRequired: false,

  /** 새로고침 등으로 진입 시, 셋업 필요 여부를 확인하고 토큰이 있으면 세션을 복원한다. */
  bootstrap: async () => {
    if (get().initialized) return;

    // 셋업 여부 판정 (무인증). 네트워크 오류 시엔 셋업 불필요로 간주해 앱을 막지 않는다.
    let setupRequired: boolean;
    try {
      setupRequired = (await getSetupStatus()).setup_required;
    } catch {
      setupRequired = false;
    }

    if (!getAccessToken()) {
      set({ initialized: true, isAuthenticated: false, setupRequired });
      return;
    }
    try {
      const user = await fetchMe();
      set({ user, isAuthenticated: true, initialized: true, setupRequired });
    } catch {
      // 토큰이 만료/무효면 인터셉터가 refresh 를 시도하고, 그래도 실패하면 여기로 온다.
      clearTokens();
      set({ user: null, isAuthenticated: false, initialized: true, setupRequired });
    }
  },

  login: async (email, password) => {
    const tokens = await apiLogin(email, password);
    setTokens(tokens.access_token, tokens.refresh_token);
    const user = await fetchMe();
    set({ user, isAuthenticated: true, initialized: true });
    return user;
  },

  logout: async () => {
    const refreshToken = getRefreshToken();
    try {
      if (refreshToken) await apiLogout(refreshToken);
    } catch {
      // 서버 폐기 실패해도 로컬 세션은 정리한다.
    }
    clearTokens();
    set({ user: null, isAuthenticated: false });
  },

  refreshUser: async () => {
    const user = await fetchMe();
    set({ user });
  },

  clearSession: () => {
    clearTokens();
    set({ user: null, isAuthenticated: false });
  },

  markSetupComplete: () => set({ setupRequired: false }),
}));

// 인터셉터의 refresh 회전 실패 시 store 세션도 비운다.
setAuthFailureHandler(() => {
  useAuthStore.getState().clearSession();
});
