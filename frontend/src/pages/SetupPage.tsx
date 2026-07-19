import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import { performSetup } from "@/api/setup";
import type { SetupResponse } from "@/api/types";
import { Spinner } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { formatBytes } from "@/lib/format";
import { useAuthStore } from "@/store/auth";
import { AuthShell } from "./LoginPage";

const GB = 1024 * 1024 * 1024;
// 서버 기본 할당량과 동일 (backend DEFAULT_MAX_STORAGE = 10GB).
const DEFAULT_QUOTA_GB = 10;

/**
 * 첫 부팅 셋업 위저드 (PRD 3.6.2, 6.1). admin 0명일 때만 접근 가능하며,
 * 라우팅 가드(App.tsx)가 setup_required 여부로 진입/재진입을 통제한다.
 */
export function SetupPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const markSetupComplete = useAuthStore((s) => s.markSetupComplete);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [signupCode, setSignupCode] = useState("");
  const [quotaGb, setQuotaGb] = useState(String(DEFAULT_QUOTA_GB));
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SetupResponse | null>(null);

  const goToLogin = () => {
    // 재진입 차단: 셋업 완료 플래그를 내린 뒤 로그인으로 이동한다.
    markSetupComplete();
    navigate("/login", { replace: true });
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (password !== passwordConfirm) {
      setError("비밀번호가 일치하지 않습니다.");
      return;
    }
    const gb = Number(quotaGb);
    if (!Number.isFinite(gb) || gb < 0) {
      setError("올바른 기본 할당량(GB)을 입력하세요.");
      return;
    }

    setSubmitting(true);
    try {
      const trimmedCode = signupCode.trim();
      const res = await performSetup({
        admin_email: email.trim(),
        admin_password: password,
        // 비우면 서버가 추측 불가 코드를 자동 생성한다.
        signup_code: trimmedCode || null,
        default_max_storage: Math.round(gb * GB),
      });
      setResult(res);
    } catch (err) {
      // 403(admin 이미 존재)/409(동시 셋업 레이스): 이미 셋업 완료 — 로그인으로 유도한다.
      const st = errorStatus(err);
      if (st === 403 || st === 409) {
        toast.error("이미 셋업이 완료되었습니다. 로그인해 주세요.");
        goToLogin();
        return;
      }
      // 422(비밀번호 정책) 등은 detail 을 그대로 보여준다.
      setError(extractErrorMessage(err, "셋업에 실패했습니다."));
    } finally {
      setSubmitting(false);
    }
  };

  const copyCode = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.signup_code);
      toast.success("가입 코드를 복사했습니다.");
    } catch {
      toast.error("복사에 실패했습니다.");
    }
  };

  if (result) {
    return (
      <AuthShell subtitle="셋업 완료">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col items-center gap-2 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full alert-success-soft text-2xl">
              ✓
            </div>
            <h2 className="text-lg font-semibold">셋업이 완료되었습니다</h2>
            <p className="text-sm text-muted">
              관리자 계정 <span className="font-medium">{result.admin_email}</span> 이(가) 생성되었습니다.
            </p>
          </div>

          <div>
            <label className="label">초기 가입 코드</label>
            <div className="flex items-center gap-2">
              <code className="flex-1 truncate rounded-lg bg-muted-token px-3 py-2 text-sm font-medium">
                {result.signup_code}
              </code>
              <button type="button" className="btn btn-secondary" onClick={copyCode}>
                복사
              </button>
            </div>
            <p className="mt-1.5 text-xs text-muted">
              구성원 가입 시 이 코드가 필요합니다. 관리 메뉴의 가입 코드 화면에서 다시 확인할 수 있습니다.
            </p>
          </div>

          <p className="text-sm text-muted">
            기본 할당량: <span className="font-medium">{formatBytes(result.default_max_storage)}</span>
          </p>

          <button type="button" className="btn btn-primary w-full" onClick={goToLogin}>
            로그인 화면으로
          </button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell subtitle="첫 부팅 설정">
      <p className="mb-4 text-sm text-muted">
        관리자 계정을 만들고 구성원 가입에 쓸 초기 가입 코드를 발급합니다. 이 화면은 최초 1회만 표시됩니다.
      </p>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor="admin-email">
            관리자 이메일
          </label>
          <input
            id="admin-email"
            type="email"
            className="input"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="label" htmlFor="admin-password">
            비밀번호
          </label>
          <input
            id="admin-password"
            type="password"
            className="input"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <p className="mt-1.5 text-xs text-muted">최소 8자, 영문·숫자·특수문자를 포함해야 합니다.</p>
        </div>
        <div>
          <label className="label" htmlFor="admin-password-confirm">
            비밀번호 확인
          </label>
          <input
            id="admin-password-confirm"
            type="password"
            className="input"
            autoComplete="new-password"
            value={passwordConfirm}
            onChange={(e) => setPasswordConfirm(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="label" htmlFor="signup-code">
            초기 가입 코드 <span className="font-normal text-muted">(선택)</span>
          </label>
          <input
            id="signup-code"
            type="text"
            className="input"
            placeholder="비우면 자동 생성됩니다"
            value={signupCode}
            onChange={(e) => setSignupCode(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="quota">
            기본 할당량
          </label>
          <div className="flex items-center gap-2">
            <input
              id="quota"
              type="number"
              min={0}
              step={1}
              className="input"
              value={quotaGb}
              onChange={(e) => setQuotaGb(e.target.value)}
              required
            />
            <span className="text-sm text-muted">GB</span>
          </div>
          <p className="mt-1.5 text-xs text-muted">신규 가입자에게 적용될 1인당 기본 저장 용량입니다.</p>
        </div>

        {error && <p className="rounded-lg alert-danger px-3 py-2 text-sm">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? <Spinner className="h-4 w-4" /> : "셋업 완료"}
        </button>
      </form>
    </AuthShell>
  );
}
