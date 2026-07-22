import { useCallback, useEffect, useState } from "react";

import { disableShare, listShares } from "@/api/admin";
import { errorStatus, extractErrorMessage } from "@/api/client";
import type { AdminShare } from "@/api/types";
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
  Spinner,
} from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { sharePermissionLabel } from "@/lib/labels";

const PAGE_SIZE = 20;

type Filter = "all" | "active" | "inactive";
const FILTER_TO_ACTIVE: Record<Filter, boolean | undefined> = {
  all: undefined,
  active: true,
  inactive: false,
};

/** admin — 전체 공유 링크 조회 + 강제 비활성화 (PRD 3.6.3 공유 링크 통제). */
export function AdminSharesPage() {
  const toast = useToast();
  const [shares, setShares] = useState<AdminShare[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [disableTarget, setDisableTarget] = useState<AdminShare | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listShares(FILTER_TO_ACTIVE[filter], undefined, page, PAGE_SIZE);
      setShares(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "공유 링크 목록을 불러오지 못했습니다."));
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

  const confirmDisable = async () => {
    if (!disableTarget) return;
    setBusy(true);
    try {
      await disableShare(disableTarget.id);
      toast.success("공유 링크를 차단했습니다.");
      setDisableTarget(null);
      await load();
    } catch (err) {
      if (errorStatus(err) !== 429) {
        toast.error(extractErrorMessage(err, "공유 링크 차단에 실패했습니다."));
      }
    } finally {
      setBusy(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 pt-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">공유 링크</h1>
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
        ) : shares.length === 0 ? (
          <EmptyState title="공유 링크가 없습니다" />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">파일</th>
                  <th className="w-56 px-4 py-2.5 font-medium">생성자</th>
                  <th className="w-20 px-4 py-2.5 font-medium">권한</th>
                  <th className="w-28 px-4 py-2.5 text-right font-medium">다운로드</th>
                  <th className="w-32 px-4 py-2.5 font-medium">만료</th>
                  <th className="w-20 px-4 py-2.5 font-medium">상태</th>
                  <th className="w-28 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {shares.map((s) => (
                  <tr key={s.id} className="border-b border-token last:border-0">
                    <td className="px-4 py-2.5">
                      <span className="truncate font-medium">{s.file_name}</span>
                    </td>
                    <td className="px-4 py-2.5 text-muted">{s.creator_email}</td>
                    <td className="px-4 py-2.5 text-muted">{sharePermissionLabel(s.permission)}</td>
                    <td className="px-4 py-2.5 text-right text-muted">
                      {s.download_count}
                      {s.max_downloads != null && ` / ${s.max_downloads}`}
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {s.expires_at ? formatDateTime(s.expires_at) : "없음"}
                    </td>
                    <td className="px-4 py-2.5">
                      {s.is_active ? (
                        <Badge tone="success">활성</Badge>
                      ) : (
                        <Badge tone="neutral">차단됨</Badge>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex justify-end">
                        {s.is_active && (
                          <button
                            className="btn btn-ghost text-danger"
                            onClick={() => setDisableTarget(s)}
                          >
                            강제 차단
                          </button>
                        )}
                      </div>
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
        open={disableTarget !== null}
        title="공유 링크 강제 차단"
        onClose={() => (busy ? undefined : setDisableTarget(null))}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setDisableTarget(null)} disabled={busy}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmDisable} disabled={busy}>
              {busy ? <Spinner className="h-4 w-4" /> : "강제 차단"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{disableTarget?.file_name}</span> 의 공유 링크를 차단합니다.
          사용자에게 알리지 않고 즉시 차단되며, 이후 공개 접근은 410으로 거부됩니다.
        </p>
      </Modal>
    </div>
  );
}
