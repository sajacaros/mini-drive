import { Navigate, Route, Routes } from "react-router-dom";

/*
 * 라우터 셸. 이후 스테이지에서 pages/ 의 실제 화면(로그인, 파일 브라우저,
 * 공유, admin 대시보드)을 여기에 연결한다.
 */
function App() {
  return (
    <Routes>
      <Route path="/" element={<Placeholder title="Mini Drive" />} />
      {/*
        <Route path="/login" element={<LoginPage />} />
        <Route path="/files" element={<FileBrowserPage />} />
        <Route path="/shares" element={<SharesPage />} />
        <Route path="/admin" element={<AdminPage />} />
      */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function Placeholder({ title }: { title: string }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-2">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="text-sm opacity-70">스캐폴딩 완료 — 화면은 이후 스테이지에서 구현됩니다.</p>
    </main>
  );
}

export default App;
