import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { disableShare, listShares } from "@/api/shares";
import type { Share } from "@/api/types";
import { buildShareLink } from "@/components/ShareModal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { CopyIcon, LinkIcon } from "@/components/icons";
import { formatDateTime } from "@/lib/format";

export function SharesPage() {
  const toast = useToast();
  const [shares, setShares] = useState<Share[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setShares(await listShares());
    } catch (err) {
      setError(extractErrorMessage(err, "공유 링크를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "비활성화에 실패했습니다."));
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <h1 className="text-lg font-semibold">공유 링크</h1>
        <p className="mt-0.5 text-sm text-muted">
          내가 만든 공유 링크를 관리합니다. 조회수·마지막 접근은 근사치입니다.
        </p>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : shares.length === 0 ? (
          <EmptyState
            icon={<LinkIcon width={40} height={40} />}
            title="아직 만든 공유 링크가 없습니다"
            hint="파일 목록에서 공유 아이콘을 눌러 링크를 만들 수 있습니다."
          />
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">파일</th>
                  <th className="w-24 px-4 py-2.5 font-medium">상태</th>
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
                    <td className="px-4 py-2.5">
                      {s.is_active ? (
                        <Badge tone="success">활성</Badge>
                      ) : (
                        <Badge tone="neutral">비활성</Badge>
                      )}
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
      </div>
    </div>
  );
}
