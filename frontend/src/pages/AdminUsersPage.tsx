import { useCallback, useEffect, useState } from "react";

import { approveUser, listUsers, rejectUser, updateUser } from "@/api/admin";
import { extractErrorMessage } from "@/api/client";
import type { AdminUser } from "@/api/types";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { formatBytes, formatDateTime } from "@/lib/format";

type Tab = "pending" | "all";
const PAGE_SIZE = 20;
const GB = 1024 * 1024 * 1024;

const STATUS_LABEL: Record<string, string> = {
  pending: "승인 대기",
  active: "활성",
  inactive: "비활성",
  rejected: "거절됨",
};

function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "active") return "success";
  if (status === "pending") return "warning";
  if (status === "rejected") return "danger";
  return "neutral";
}

export function AdminUsersPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("pending");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quotaTarget, setQuotaTarget] = useState<AdminUser | null>(null);
  const [quotaGb, setQuotaGb] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listUsers(tab === "pending" ? "pending" : undefined, page, PAGE_SIZE);
      setUsers(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "사용자 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [tab, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const switchTab = (next: Tab) => {
    setTab(next);
    setPage(1);
  };

  const onApprove = async (u: AdminUser) => {
    try {
      await approveUser(u.id);
      toast.success(`${u.email} 승인 완료`);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "승인에 실패했습니다."));
    }
  };

  const onReject = async (u: AdminUser) => {
    try {
      await rejectUser(u.id);
      toast.success(`${u.email} 거절 처리`);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "거절에 실패했습니다."));
    }
  };

  const onToggleActive = async (u: AdminUser) => {
    const next = u.status === "active" ? "inactive" : "active";
    try {
      await updateUser(u.id, { status: next });
      toast.success(next === "active" ? "활성화했습니다." : "비활성화했습니다.");
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "상태 변경에 실패했습니다."));
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

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 pt-4">
        <h1 className="text-lg font-semibold">사용자 관리</h1>
        <div className="mt-3 flex gap-1">
          <TabButton active={tab === "pending"} onClick={() => switchTab("pending")}>
            승인 대기
          </TabButton>
          <TabButton active={tab === "all"} onClick={() => switchTab("all")}>
            전체
          </TabButton>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : users.length === 0 ? (
          <EmptyState title={tab === "pending" ? "승인 대기 중인 사용자가 없습니다" : "사용자가 없습니다"} />
        ) : (
          <div className="card overflow-hidden">
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
                      {u.role === "admin" ? <Badge tone="accent">admin</Badge> : <span className="text-muted">user</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge tone={statusTone(u.status)}>{STATUS_LABEL[u.status] ?? u.status}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {formatBytes(u.storage_used)} / {formatBytes(u.max_storage)}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{formatDateTime(u.created_at)}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex flex-wrap justify-end gap-2">
                        {u.status === "pending" ? (
                          <>
                            <button className="btn btn-primary" onClick={() => onApprove(u)}>
                              승인
                            </button>
                            <button className="btn btn-ghost text-red-600" onClick={() => onReject(u)}>
                              거절
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              className="btn btn-secondary"
                              onClick={() => {
                                setQuotaTarget(u);
                                setQuotaGb((u.max_storage / GB).toFixed(1));
                              }}
                            >
                              할당량
                            </button>
                            {(u.status === "active" || u.status === "inactive") && (
                              <button
                                className={u.status === "active" ? "btn btn-ghost text-red-600" : "btn btn-secondary"}
                                onClick={() => onToggleActive(u)}
                              >
                                {u.status === "active" ? "비활성화" : "활성화"}
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-center gap-3 text-sm">
            <button className="btn btn-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              이전
            </button>
            <span className="text-muted">
              {page} / {totalPages}
            </span>
            <button
              className="btn btn-secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </button>
          </div>
        )}
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
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
        active
          ? "border-[color:var(--accent)] text-[color:var(--accent)]"
          : "border-transparent text-muted hover:text-[color:var(--text-primary)]"
      }`}
    >
      {children}
    </button>
  );
}
