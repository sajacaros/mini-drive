import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { register } from "@/api/auth";
import { extractErrorMessage } from "@/api/client";
import { Spinner } from "@/components/ui";
import { AuthShell } from "./LoginPage";

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register({
        email: email.trim(),
        password,
        display_name: displayName.trim(),
      });
      setDone(true);
    } catch (err) {
      // 409(중복 이메일) / 422(비밀번호 정책) 등 detail 로 구분되어 온다.
      setError(extractErrorMessage(err, "가입 신청에 실패했습니다."));
    } finally {
      setSubmitting(false);
    }
  };

  if (done) {
    return (
      <AuthShell subtitle="가입 신청 완료">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full alert-success-soft text-2xl">
            ✓
          </div>
          <h2 className="text-lg font-semibold">가입 신청이 접수되었습니다</h2>
          <p className="text-sm text-muted">
            관리자 승인 후 이용 가능합니다. 승인이 완료되면 로그인해 주세요.
          </p>
          <Link to="/login" className="btn btn-primary mt-2 w-full">
            로그인 화면으로
          </Link>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell subtitle="가입 신청 (승인제)">
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
          <label className="label" htmlFor="displayName">
            표시 이름
          </label>
          <input
            id="displayName"
            type="text"
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
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
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <p className="mt-1.5 text-xs text-muted">
            최소 8자, 영문·숫자·특수문자를 포함해야 합니다.
          </p>
        </div>

        {error && (
          <p className="rounded-lg alert-danger px-3 py-2 text-sm">{error}</p>
        )}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? <Spinner className="h-4 w-4" /> : "가입 신청"}
        </button>
      </form>

      <p className="mt-5 text-center text-sm text-muted">
        이미 계정이 있으신가요?{" "}
        <Link to="/login" className="font-medium text-[color:var(--accent)]">
          로그인
        </Link>
      </p>
    </AuthShell>
  );
}
