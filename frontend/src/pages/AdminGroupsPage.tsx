import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listGroups } from "@/api/admin";
import { extractErrorMessage } from "@/api/client";
import type { AdminGroup } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { Badge, EmptyState, ErrorState, LoadingState, Pagination } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const PAGE_SIZE = 20;

/** admin — 전체 그룹 조회 (owner 아니어도 현황 확인) — PRD 3.6.3. */
export function AdminGroupsPage() {
  const navigate = useNavigate();
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listGroups(includeInactive, page, PAGE_SIZE);
      setGroups(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "그룹 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [includeInactive, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <div className="flex items-center justify-between gap-4">
            <div>
              <h1 className="text-lg font-semibold">그룹</h1>
              <p className="mt-0.5 text-sm text-muted">전체 {total}개</p>
            </div>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(e) => {
                  setIncludeInactive(e.target.checked);
                  setPage(1);
                }}
              />
              비활성 포함
            </label>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : groups.length === 0 ? (
          <EmptyState title="그룹이 없습니다" />
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">그룹</th>
                  <th className="w-56 px-4 py-2.5 font-medium">소유자</th>
                  <th className="w-20 px-4 py-2.5 text-right font-medium">멤버</th>
                  <th className="w-20 px-4 py-2.5 text-right font-medium">파일</th>
                  <th className="w-24 px-4 py-2.5 font-medium">상태</th>
                  <th className="w-32 px-4 py-2.5 font-medium">생성일</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <tr
                    key={g.id}
                    className="cursor-pointer border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)]"
                    onClick={() => navigate(`/groups/${g.id}`)}
                  >
                    <td className="px-4 py-2.5">
                      <div className="flex flex-col">
                        <span className="font-medium">{g.name}</span>
                        {g.description && (
                          <span className="truncate text-xs text-muted">{g.description}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-muted">{g.owner_email}</td>
                    <td className="px-4 py-2.5 text-right text-muted">{g.member_count}</td>
                    <td className="px-4 py-2.5 text-right text-muted">{g.file_count}</td>
                    <td className="px-4 py-2.5">
                      {g.is_active ? (
                        <Badge tone="success">활성</Badge>
                      ) : (
                        <Badge tone="neutral">비활성</Badge>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{formatDateTime(g.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>
    </div>
  );
}
