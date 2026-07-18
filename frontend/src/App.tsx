import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AdminRoute, ProtectedRoute } from "@/components/ProtectedRoute";
import { Layout } from "@/components/Layout";
import { Toaster } from "@/components/Toast";
import { LoadingState } from "@/components/ui";
import { AdminUsersPage } from "@/pages/AdminUsersPage";
import { FileBrowserPage } from "@/pages/FileBrowserPage";
import { LoginPage } from "@/pages/LoginPage";
import { PublicSharePage } from "@/pages/PublicSharePage";
import { RegisterPage } from "@/pages/RegisterPage";
import { SharesPage } from "@/pages/SharesPage";
import { TrashPage } from "@/pages/TrashPage";
import { useAuthStore } from "@/store/auth";

function App() {
  const { initialized, bootstrap } = useAuthStore();

  // 앱 진입 시 저장된 토큰으로 세션 복원 (me 조회).
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  if (!initialized) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LoadingState label="시작하는 중..." />
      </div>
    );
  }

  return (
    <>
      <Routes>
        {/* 무인증 */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/s/:shareUrl" element={<PublicSharePage />} />

        {/* 인증 필요 (공통 레이아웃) */}
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/" element={<FileBrowserPage />} />
            <Route path="/trash" element={<TrashPage />} />
            <Route path="/shares" element={<SharesPage />} />
          </Route>
        </Route>

        {/* admin 전용 */}
        <Route element={<AdminRoute />}>
          <Route element={<Layout />}>
            <Route path="/admin/users" element={<AdminUsersPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  );
}

export default App;
