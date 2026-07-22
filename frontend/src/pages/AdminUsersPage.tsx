import { useCallback, useEffect, useState } from "react";

import { listUsers, updateUser } from "@/api/admin";
import { extractErrorMessage } from "@/api/client";
import type { AdminUser, UserStatus } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import {
  Badge,
  EmptyState,
  ErrorState,
  FilterTab,
  LoadingState,
  Pagination,
} from "@/components/ui";
import { formatBytes, formatDateTime } from "@/lib/format";
import {
  globalRoleLabel,
  globalRoleTone,
  userStatusLabel,
  userStatusTone,
} from "@/lib/labels";
import { useAuthStore } from "@/store/auth";

type Filter = "all" | "active" | "inactive";
const FILTER_TO_STATUS: Record<Filter, UserStatus | undefined> = {
  all: undefined,
  active: "active",
  inactive: "inactive",
};

const PAGE_SIZE = 20;
const GB = 1024 * 1024 * 1024;

export function AdminUsersPage() {
  const toast = useToast();
  const me = useAuthStore((s) => s.user);
  // 관리자 권한 부여/회수는 최고 관리자(super_admin)만 가능.
  const isSuperAdmin = me?.role === "super_admin";
  const [filter, setFilter] = useState<Filter>("all");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quotaTarget, setQuotaTarget] = useState<AdminUser | null>(null);
  const [quotaGb, setQuotaGb] = useState("");
  const [nameTarget, setNameTarget] = useState<AdminUser | null>(null);
  const [nameValue, setNameValue] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listUsers(FILTER_TO_STATUS[filter], page, PAGE_SIZE);
      setUsers(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "사용자 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [filter, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const switchFilter = (next: Filter) => {
    setFilter(next);
    setPage(1);
  };

  const onToggleActive = async (u: AdminUser) => {
    const next: UserStatus = u.status === "active" ? "inactive" : "active";
    try {
      await updateUser(u.id, { status: next });
      toast.success(next === "active" ? "활성화했습니다." : "비활성화했습니다.");
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "상태 변경에 실패했습니다."));
    }
  };

  // user ↔ admin 토글 (super_admin 전용). super_admin 계정은 대상이 되지 않는다.
  const onToggleRole = async (u: AdminUser) => {
    const next = u.role === "admin" ? "user" : "admin";
    try {
      await updateUser(u.id, { role: next });
      toast.success(next === "admin" ? "관리자로 지정했습니다." : "관리자를 해제했습니다.");
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "역할 변경에 실패했습니다."));
    }
  };

  const submitQuota = async () => {
    if (!quotaTarget) return;
    const gb = Number(quotaGb);
    if (!Number.isFinite(gb) || gb < 0) {
      toast.error("올바른 용량을 입력하세요.");
      return;
    }
    try {
      await updateUser(quotaTarget.id, { max_storage: Math.round(gb * GB) });
      toast.success("할당량을 변경했습니다.");
      setQuotaTarget(null);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "할당량 변경에 실패했습니다."));
    }
  };

  const submitName = async () => {
    if (!nameTarget) return;
    const trimmed = nameValue.trim();
    if (!trimmed) {
      toast.error("이름을 입력하세요.");
      return;
    }
    if (trimmed === nameTarget.display_name) {
      setNameTarget(null);
      return;
    }
    try {
      await updateUser(nameTarget.id, { display_name: trimmed });
      toast.success("이름을 변경했습니다.");
      setNameTarget(null);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "이름 변경에 실패했습니다."));
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 pt-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">사용자 관리</h1>
          <div className="mt-3 flex gap-1">
            <FilterTab active={filter === "all"} onClick={() => switchFilter("all")}>
              전체
            </FilterTab>
            <FilterTab active={filter === "active"} onClick={() => switchFilter("active")}>
              활성
            </FilterTab>
            <FilterTab active={filter === "inactive"} onClick={() => switchFilter("inactive")}>
              비활성
            </FilterTab>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : users.length === 0 ? (
          <EmptyState title="사용자가 없습니다" />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">사용자</th>
                  <th className="w-24 px-4 py-2.5 font-medium">역할</th>
                  <th className="w-24 px-4 py-2.5 font-medium">상태</th>
                  <th className="w-44 px-4 py-2.5 font-medium">사용량</th>
                  <th className="w-32 px-4 py-2.5 font-medium">가입일</th>
                  <th className="w-56 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-b border-token last:border-0">
                    <td className="px-4 py-2.5">
                      <div className="flex flex-col">
                        <span className="font-medium">{u.display_name || "-"}</span>
                        <span className="text-xs text-muted">{u.email}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      {u.role === "user" ? (
                        <span className="text-muted">일반</span>
                      ) : (
                        <Badge tone={globalRoleTone(u.role)}>{globalRoleLabel(u.role)}</Badge>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge tone={userStatusTone(u.status)}>{userStatusLabel(u.status)}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {formatBytes(u.storage_used)} / {formatBytes(u.max_storage)}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{formatDateTime(u.created_at)}</td>
                    <td className="px-4 py-2.5">
                      {u.role === "super_admin" ? (
                        // 최고 관리자 계정은 다른 관리자가 수정할 수 없다(보호).
                        <div className="flex justify-end">
                          <span className="text-xs text-muted">보호된 계정</span>
                        </div>
                      ) : (
                        <div className="flex flex-wrap justify-end gap-2">
                          {isSuperAdmin && (
                            <button
                              className="btn btn-secondary"
                              onClick={() => {
                                setNameTarget(u);
                                setNameValue(u.display_name);
                              }}
                            >
                              이름 수정
                            </button>
                          )}
                          <button
                            className="btn btn-secondary"
                            onClick={() => {
                              setQuotaTarget(u);
                              setQuotaGb((u.max_storage / GB).toFixed(1));
                            }}
                          >
                            할당량
                          </button>
                          {isSuperAdmin && (
                            <button className="btn btn-secondary" onClick={() => onToggleRole(u)}>
                              {u.role === "admin" ? "관리자 해제" : "관리자 지정"}
                            </button>
                          )}
                          <button
                            className={u.status === "active" ? "btn btn-ghost text-danger" : "btn btn-secondary"}
                            onClick={() => onToggleActive(u)}
                          >
                            {u.status === "active" ? "비활성화" : "활성화"}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>

      <Modal
        open={quotaTarget !== null}
        title="할당량 변경"
        onClose={() => setQuotaTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setQuotaTarget(null)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitQuota}>
              저장
            </button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          {quotaTarget?.email} 의 최대 저장 용량(GB)을 설정합니다.
        </p>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            step={0.5}
            className="input"
            value={quotaGb}
            onChange={(e) => setQuotaGb(e.target.value)}
            autoFocus
          />
          <span className="text-sm text-muted">GB</span>
        </div>
      </Modal>

      <Modal
        open={nameTarget !== null}
        title="이름 수정"
        onClose={() => setNameTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setNameTarget(null)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitName}>
              저장
            </button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          {nameTarget?.email} 의 표시 이름을 변경합니다.
        </p>
        <input
          type="text"
          className="input"
          maxLength={100}
          value={nameValue}
          onChange={(e) => setNameValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submitName();
          }}
          autoFocus
        />
      </Modal>
    </div>
  );
}
