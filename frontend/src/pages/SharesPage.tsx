import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { disableShare, listShares } from "@/api/shares";
import type { Share } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { buildShareLink } from "@/components/ShareModal";
import { useToast } from "@/components/Toast";
import {
  EmptyState,
  ErrorState,
  FilterTab,
  LoadingState,
  Pagination,
} from "@/components/ui";
import { CopyIcon, LinkIcon } from "@/components/icons";
import { formatDateTime } from "@/lib/format";

const PAGE_SIZE = 20;

type Tab = "active" | "inactive";

export function SharesPage() {
  const toast = useToast();
  const [shares, setShares] = useState<Share[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [tab, setTab] = useState<Tab>("active");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listShares(tab === "active", page, PAGE_SIZE);
      setShares(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "공유 링크를 불러오지 못했습니다."));
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

  const copy = async (share: Share) => {
    try {
      await navigator.clipboard.writeText(buildShareLink(share.share_url));
      toast.success("링크를 복사했습니다.");
    } catch {
      toast.error("클립보드 복사에 실패했습니다.");
    }
  };

  const onDisable = async (share: Share) => {
    try {
      await disableShare(share.id);
      toast.success("공유를 비활성화했습니다.");
      // 활성 탭에서 방금 비활성화한 항목은 목록에서 빠진다. 그게 이 페이지의 마지막 한 건이었다면
      // 빈 페이지가 남으므로 앞 페이지로 물러난다(setPage 가 load 를 다시 태운다).
      if (shares.length === 1 && page > 1) setPage(page - 1);
      else await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "비활성화에 실패했습니다."));
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 pt-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">공유 링크</h1>
          <p className="mt-0.5 text-sm text-muted">
            내가 만든 공유 링크를 관리합니다. 조회수·마지막 접근은 근사치입니다.
          </p>
          <div className="mt-3 flex gap-1">
            <FilterTab active={tab === "active"} onClick={() => switchTab("active")}>
              활성
            </FilterTab>
            <FilterTab active={tab === "inactive"} onClick={() => switchTab("inactive")}>
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
          tab === "active" ? (
            <EmptyState
              icon={<LinkIcon width={40} height={40} />}
              title="활성 공유 링크가 없습니다"
              hint="파일 목록에서 공유 아이콘을 눌러 링크를 만들 수 있습니다."
            />
          ) : (
            <EmptyState
              icon={<LinkIcon width={40} height={40} />}
              title="비활성 공유 링크가 없습니다"
              hint="비활성화한 링크는 이력 보존을 위해 이 탭에 남습니다."
            />
          )
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">파일</th>
                  <th className="w-24 px-4 py-2.5 font-medium">다운로드</th>
                  <th className="w-20 px-4 py-2.5 font-medium">조회수</th>
                  <th className="w-44 px-4 py-2.5 font-medium">마지막 접근</th>
                  <th className="w-40 px-4 py-2.5 font-medium">생성일</th>
                  <th className="w-44 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {shares.map((s) => (
                  <tr key={s.id} className="border-b border-token last:border-0">
                    <td className="px-4 py-2.5">
                      <div className="flex flex-col">
                        <span className="truncate font-medium">{s.file_name}</span>
                        <span className="truncate text-xs text-muted">
                          {s.permission === "read" ? "읽기 전용" : "다운로드 허용"}
                          {s.password_required && " · 비밀번호"}
                          {s.expires_at && ` · ${formatDateTime(s.expires_at)} 만료`}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {s.download_count}
                      {s.max_downloads != null ? ` / ${s.max_downloads}` : ""}
                    </td>
                    <td className="px-4 py-2.5 text-muted">~{s.view_count}</td>
                    <td className="px-4 py-2.5 text-muted">
                      {s.last_access_at ? formatDateTime(s.last_access_at) : "-"}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{formatDateTime(s.created_at)}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex justify-end gap-2">
                        <button className="btn btn-secondary" onClick={() => copy(s)}>
                          <CopyIcon width={16} height={16} />
                          복사
                        </button>
                        {s.is_active && (
                          <button className="btn btn-ghost text-danger" onClick={() => onDisable(s)}>
                            비활성화
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
    </div>
  );
}
