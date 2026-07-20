import { useState, type FormEvent } from "react";

import { extractErrorMessage } from "@/api/client";
import { updateMe } from "@/api/users";
import { Badge, Spinner } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { globalRoleLabel, globalRoleTone } from "@/lib/labels";
import { useAuthStore } from "@/store/auth";

/**
 * 내 프로필 — 표시 이름 편집. 셋업 admin 의 기본 이름("Administrator")을 실명으로
 * 바꾸는 경로. 저장 후 store 의 refreshUser 로 사이드바 등 이름 표시를 갱신한다.
 */
export function ProfilePage() {
  const toast = useToast();
  const user = useAuthStore((s) => s.user);
  const refreshUser = useAuthStore((s) => s.refreshUser);

  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [saving, setSaving] = useState(false);

  const dirty = displayName.trim() !== (user?.display_name ?? "");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = displayName.trim();
    if (!trimmed) {
      toast.error("이름을 입력하세요.");
      return;
    }
    setSaving(true);
    try {
      await updateMe(trimmed);
      await refreshUser();
      toast.success("프로필을 저장했습니다.");
    } catch (err) {
      toast.error(extractErrorMessage(err, "프로필 저장에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <h1 className="text-lg font-semibold">내 프로필</h1>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="card mx-auto max-w-lg p-6">
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <div>
              <label className="label" htmlFor="displayName">
                이름
              </label>
              <input
                id="displayName"
                className="input"
                type="text"
                maxLength={100}
                placeholder="표시할 이름"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                autoFocus
              />
              <p className="mt-1.5 text-xs text-muted">
                드라이브·그룹 등에서 다른 구성원에게 표시되는 이름입니다.
              </p>
            </div>

            <div>
              <label className="label">이메일</label>
              <div className="flex items-center gap-2 rounded-lg bg-muted-token px-3 py-2 text-sm">
                <span className="truncate">{user?.email}</span>
                {user?.role && user.role !== "user" && (
                  <Badge tone={globalRoleTone(user.role)}>
                    {globalRoleLabel(user.role)}
                  </Badge>
                )}
              </div>
              <p className="mt-1.5 text-xs text-muted">이메일은 변경할 수 없습니다.</p>
            </div>

            <div className="flex justify-end">
              <button
                type="submit"
                className="btn btn-primary"
                disabled={saving || !dirty}
              >
                {saving ? <Spinner className="h-4 w-4" /> : "저장"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
