/** 인증/관리자 라우트 가드. */

import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuthStore } from "@/store/auth";
import { LoadingState } from "./ui";

export function ProtectedRoute() {
  const { initialized, isAuthenticated } = useAuthStore();
  const location = useLocation();

  if (!initialized) return <LoadingState label="세션 확인 중..." />;
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

export function AdminRoute() {
  const { initialized, isAuthenticated, user } = useAuthStore();

  if (!initialized) return <LoadingState label="세션 확인 중..." />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  // admin/super_admin 모두 관리 화면 접근 허용.
  if (user?.role !== "admin" && user?.role !== "super_admin") {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}
