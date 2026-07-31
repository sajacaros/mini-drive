import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AdminRoute, ProtectedRoute } from "@/components/ProtectedRoute";
import { Layout } from "@/components/Layout";
import { Toaster } from "@/components/Toast";
import { LoadingState } from "@/components/ui";
import { AdminAuditPage } from "@/pages/AdminAuditPage";
import { AdminDashboardPage } from "@/pages/AdminDashboardPage";
import { AdminGroupsPage } from "@/pages/AdminGroupsPage";
import { AdminSharesPage } from "@/pages/AdminSharesPage";
import { AdminSignupCodesPage } from "@/pages/AdminSignupCodesPage";
import { AdminUsersPage } from "@/pages/AdminUsersPage";
import { FavoritesPage } from "@/pages/FavoritesPage";
import { FileBrowserPage, SharedFolderBrowserPage } from "@/pages/FileBrowserPage";
import { GroupDetailPage } from "@/pages/GroupDetailPage";
import { GroupsPage } from "@/pages/GroupsPage";
import { LoginPage } from "@/pages/LoginPage";
import { PublicSharePage } from "@/pages/PublicSharePage";
import { RecentPage } from "@/pages/RecentPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { RoutinesPage } from "@/pages/RoutinesPage";
import { SetupPage } from "@/pages/SetupPage";
import { SharesPage } from "@/pages/SharesPage";
import { TodoMonthPage } from "@/pages/TodoMonthPage";
import { TodoPage } from "@/pages/TodoPage";
import { TodoReportsPage } from "@/pages/TodoReportsPage";
import { TrashPage } from "@/pages/TrashPage";
import { WikiCatalogDetailPage } from "@/pages/WikiCatalogDetailPage";
import { WikiCatalogPage } from "@/pages/WikiCatalogPage";
import { WikiPage } from "@/pages/WikiPage";
import { useAuthStore } from "@/store/auth";

function App() {
  const { initialized, bootstrap, setupRequired } = useAuthStore();

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

  // 첫 부팅 셋업 미완료(admin 0명)면 어디로 가든 /setup 으로 유도한다 (PRD 3.6.2).
  if (setupRequired) {
    return (
      <>
        <Routes>
          <Route path="/setup" element={<SetupPage />} />
          <Route path="*" element={<Navigate to="/setup" replace />} />
        </Routes>
        <Toaster />
      </>
    );
  }

  return (
    <>
      <Routes>
        {/* 무인증 */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        {/* 셋업 완료 후 재진입 차단. */}
        <Route path="/setup" element={<Navigate to="/login" replace />} />
        <Route path="/s/:shareUrl" element={<PublicSharePage />} />

        {/* 인증 필요 (공통 레이아웃) */}
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/" element={<FileBrowserPage />} />
            {/* 폴더는 주소에 남는다 — 뒤로가기가 상위 폴더를 뜻하고 링크로 그 자리를 연다. */}
            <Route path="/f/:folderId" element={<FileBrowserPage />} />
            <Route path="/todo" element={<TodoPage />} />
            <Route path="/todo/month" element={<TodoMonthPage />} />
            <Route path="/routines" element={<RoutinesPage />} />
            <Route path="/todo/reports" element={<TodoReportsPage />} />
            <Route path="/favorites" element={<FavoritesPage />} />
            <Route path="/recent" element={<RecentPage />} />
            <Route path="/trash" element={<TrashPage />} />
            <Route path="/shares" element={<SharesPage />} />
            <Route path="/wiki" element={<WikiPage />} />
            <Route path="/wiki/catalog" element={<WikiCatalogPage />} />
            <Route path="/wiki/catalog/:fileId" element={<WikiCatalogDetailPage />} />
            {/* "공유됨"은 내 드라이브 > 공유 가상 폴더다 — 실제 폴더 id 가 없어 고유 경로를 준다. */}
            <Route path="/shared" element={<FileBrowserPage />} />
            {/* 옛 공유 폴더 딥링크 — 폴더는 이제 /f/:id 하나로 통일됐다. */}
            <Route path="/shared/f/:fileId" element={<SharedFolderBrowserPage />} />
            <Route path="/groups" element={<GroupsPage />} />
            <Route path="/groups/:id" element={<GroupDetailPage />} />
            {/* 프로필은 모달로 전환됨 — 딥링크/구링크는 홈으로. */}
            <Route path="/profile" element={<Navigate to="/" replace />} />
          </Route>
        </Route>

        {/* admin 전용 */}
        <Route element={<AdminRoute />}>
          <Route element={<Layout />}>
            <Route path="/admin" element={<AdminDashboardPage />} />
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/admin/signup-codes" element={<AdminSignupCodesPage />} />
            <Route path="/admin/groups" element={<AdminGroupsPage />} />
            <Route path="/admin/shares" element={<AdminSharesPage />} />
            <Route path="/admin/audit" element={<AdminAuditPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  );
}

export default App;
