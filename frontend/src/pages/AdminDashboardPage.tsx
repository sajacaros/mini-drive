import { useCallback, useEffect, useState } from "react";

import { getStats } from "@/api/admin";
import { extractErrorMessage } from "@/api/client";
import type { AdminStats } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { Badge, ErrorState, LoadingState } from "@/components/ui";
import { formatBytes, formatPercent } from "@/lib/format";
import { userStatusLabel, userStatusTone } from "@/lib/labels";

/** admin 대시보드 — 인스턴스 통계 카드 + 사용량 상위 사용자 (PRD 3.6.3 스토리지 통계). */
export function AdminDashboardPage() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStats(await getStats());
    } catch (err) {
      setError(extractErrorMessage(err, "통계를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">대시보드</h1>
          <p className="mt-0.5 text-sm text-muted">인스턴스 사용 현황 (메타데이터 집계)</p>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error || !stats ? (
          <ErrorState message={error ?? "통계를 불러오지 못했습니다."} onRetry={load} />
        ) : (
          <div className="flex flex-col gap-6">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
              <StatCard label="사용자" value={String(stats.total_users)}>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(stats.users_by_status).map(([status, count]) => (
                    <Badge key={status} tone={userStatusTone(status)}>
                      {userStatusLabel(status)} {count}
                    </Badge>
                  ))}
                </div>
              </StatCard>
              <StatCard label="총 저장 용량" value={formatBytes(stats.total_storage_used)} />
              <StatCard label="파일" value={stats.total_files.toLocaleString()} />
              <StatCard label="폴더" value={stats.total_folders.toLocaleString()} />
              <StatCard label="그룹" value={String(stats.total_groups)} />
              <StatCard label="공유 링크" value={String(stats.total_shares.total)}>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge tone="success">활성 {stats.total_shares.active}</Badge>
                  <Badge tone="neutral">비활성 {stats.total_shares.inactive}</Badge>
                </div>
              </StatCard>
            </div>

            <div>
              <h2 className="mb-3 text-sm font-semibold">사용량 상위 사용자</h2>
              {stats.top_users.length === 0 ? (
                <p className="text-sm text-muted">데이터가 없습니다.</p>
              ) : (
                <div className="card overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-token text-left text-xs text-muted">
                        <th className="px-4 py-2.5 font-medium">사용자</th>
                        <th className="w-64 px-4 py-2.5 font-medium">사용률</th>
                        <th className="w-40 px-4 py-2.5 text-right font-medium">사용량</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.top_users.map((u) => {
                        const ratio = u.max_storage > 0 ? u.storage_used / u.max_storage : 0;
                        return (
                          <tr key={u.email} className="border-b border-token last:border-0">
                            <td className="px-4 py-2.5">{u.email}</td>
                            <td className="px-4 py-2.5">
                              <div className="flex items-center gap-2">
                                <div className="h-2 w-full overflow-hidden rounded-full bg-muted-token">
                                  <div
                                    className="h-full rounded-full"
                                    style={{
                                      width: formatPercent(ratio),
                                      background:
                                        ratio > 0.9 ? "var(--danger)" : "var(--accent)",
                                    }}
                                  />
                                </div>
                                <span className="w-10 shrink-0 text-right text-xs text-muted">
                                  {formatPercent(ratio)}
                                </span>
                              </div>
                            </td>
                            <td className="px-4 py-2.5 text-right text-muted">
                              {formatBytes(u.storage_used)} / {formatBytes(u.max_storage)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="card p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {children}
    </div>
  );
}
