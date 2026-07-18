import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import { DriveIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import { useAuthStore } from "@/store/auth";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((s) => s.login);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      // 403: 승인 대기/비활성·거절 메시지가 detail 로 구분되어 온다 (auth 라우터).
      setError(extractErrorMessage(err, "로그인에 실패했습니다."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthShell subtitle="사내 파일 공유 서비스">
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor="email">
            이메일
          </label>
          <input
            id="email"
            type="email"
            className="input"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="label" htmlFor="password">
            비밀번호
          </label>
          <input
            id="password"
            type="password"
            className="input"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        {error && (
          <p className="rounded-lg alert-danger px-3 py-2 text-sm">{error}</p>
        )}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? <Spinner className="h-4 w-4" /> : "로그인"}
        </button>
      </form>

      <p className="mt-5 text-center text-sm text-muted">
        아직 계정이 없으신가요?{" "}
        <Link to="/register" className="font-medium text-[color:var(--accent)]">
          가입 신청
        </Link>
      </p>
    </AuthShell>
  );
}

/** 인증 화면 공통 셸 — 중앙 카드. RegisterPage 와 공유. */
export function AuthShell({
  children,
  subtitle,
}: {
  children: React.ReactNode;
  subtitle: string;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2">
          <span className="text-[color:var(--accent)]">
            <DriveIcon width={32} height={32} />
          </span>
          <h1 className="text-xl font-semibold">Mini Drive</h1>
          <p className="text-sm text-muted">{subtitle}</p>
        </div>
        <div className="card p-6">{children}</div>
      </div>
    </div>
  );
}
