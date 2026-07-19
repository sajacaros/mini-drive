import { useCallback, useEffect, useState } from "react";

import { listAuditLogs, type AuditLogFilter } from "@/api/admin";
import { extractErrorMessage } from "@/api/client";
import type { AdminAuditLog } from "@/api/types";
import { Badge, EmptyState, ErrorState, LoadingState, Pagination } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { auditActionLabel, auditActionTone, targetTypeLabel } from "@/lib/labels";

const PAGE_SIZE = 20;

/** 필터 선택지 — 백엔드 audit action / target_type 과 대응. */
const ACTION_OPTIONS = [
  "user.approve",
  "user.reject",
  "user.activate",
  "user.deactivate",
  "user.quota_update",
  "user.role_update",
  "share.force_disable",
  "permission.grant",
  "permission.revoke",
  "signup_code.create",
  "signup_code.update",
];
const TARGET_OPTIONS = ["user", "group", "file", "share", "signup_code"];

/** admin — 감사 로그 조회 (PRD 3.6.3 / 5.9). */
export function AdminAuditPage() {
  const [logs, setLogs] = useState<AdminAuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const filter: AuditLogFilter = {
        action: action || undefined,
        targetType: targetType || undefined,
        // datetime-local 값을 ISO 로 변환 (미입력 시 생략).
        from: from ? new Date(from).toISOString() : undefined,
        to: to ? new Date(to).toISOString() : undefined,
      };
      const res = await listAuditLogs(filter, page, PAGE_SIZE);
      setLogs(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "감사 로그를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [action, targetType, from, to, page]);

  useEffect(() => {
    void load();
  }, [load]);

  /** 필터 변경 시 1페이지로 되돌린다. */
  const onFilterChange = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setPage(1);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <h1 className="text-lg font-semibold">감사 로그</h1>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs text-muted">액션</label>
            <select
              className="input py-1.5"
              value={action}
              onChange={(e) => onFilterChange(setAction)(e.target.value)}
            >
              <option value="">전체</option>
              {ACTION_OPTIONS.map((a) => (
                <option key={a} value={a}>
                  {auditActionLabel(a)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">대상 유형</label>
            <select
              className="input py-1.5"
              value={targetType}
              onChange={(e) => onFilterChange(setTargetType)(e.target.value)}
            >
              <option value="">전체</option>
              {TARGET_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {targetTypeLabel(t)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">시작</label>
            <input
              type="datetime-local"
              className="input py-1.5"
              value={from}
              onChange={(e) => onFilterChange(setFrom)(e.target.value)}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">종료</label>
            <input
              type="datetime-local"
              className="input py-1.5"
              value={to}
              onChange={(e) => onFilterChange(setTo)(e.target.value)}
            />
          </div>
          {(action || targetType || from || to) && (
            <button
              className="btn btn-ghost"
              onClick={() => {
                setAction("");
                setTargetType("");
                setFrom("");
                setTo("");
                setPage(1);
              }}
            >
              초기화
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : logs.length === 0 ? (
          <EmptyState title="감사 로그가 없습니다" />
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="w-40 px-4 py-2.5 font-medium">시각</th>
                  <th className="w-48 px-4 py-2.5 font-medium">수행자</th>
                  <th className="w-32 px-4 py-2.5 font-medium">액션</th>
                  <th className="w-32 px-4 py-2.5 font-medium">대상</th>
                  <th className="px-4 py-2.5 font-medium">상세</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => {
                  const isOpen = expanded === log.id;
                  const hasDetail = log.detail && Object.keys(log.detail).length > 0;
                  return (
                    <tr key={log.id} className="border-b border-token align-top last:border-0">
                      <td className="px-4 py-2.5 text-muted">{formatDateTime(log.created_at)}</td>
                      <td className="px-4 py-2.5">{log.actor_email}</td>
                      <td className="px-4 py-2.5">
                        <Badge tone={auditActionTone(log.action)}>
                          {auditActionLabel(log.action)}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-muted">
                        {targetTypeLabel(log.target_type)}
                        {log.target_id != null && ` #${log.target_id}`}
                      </td>
                      <td className="px-4 py-2.5">
                        {hasDetail ? (
                          <div>
                            <button
                              className="text-xs text-[color:var(--accent)] hover:underline"
                              onClick={() => setExpanded(isOpen ? null : log.id)}
                            >
                              {isOpen ? "접기" : "펼치기"}
                            </button>
                            {isOpen && (
                              <pre className="mt-1.5 max-w-xl overflow-x-auto rounded-lg bg-muted-token p-2 text-xs">
                                {JSON.stringify(log.detail, null, 2)}
                              </pre>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-muted">-</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>
    </div>
  );
}
