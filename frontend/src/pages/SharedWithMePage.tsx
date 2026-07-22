import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import { listSharedWithMe } from "@/api/permissions";
import type { SharedItem } from "@/api/types";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { downloadFile } from "@/lib/download";
import {
  DownloadIcon,
  FileIcon,
  FolderIcon,
  InboxIcon,
} from "@/components/icons";
import { formatBytes, formatDateTime } from "@/lib/format";
import { permissionCovers, permissionLabel, permissionTone } from "@/lib/labels";

export function SharedWithMePage() {
  const toast = useToast();
  const navigate = useNavigate();

  const [items, setItems] = useState<SharedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSharedWithMe();
      setItems(res.items);
    } catch (err) {
      setError(extractErrorMessage(err, "공유된 항목을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onOpen = (item: SharedItem) => {
    if (item.file.is_folder) {
      // 공유 폴더로 진입 — 권한 게이팅된 파일 브라우저(내 권한은 진입 후 재확인).
      navigate(`/shared/f/${item.file.id}`, {
        state: { name: item.file.name, permission: item.permission },
      });
    }
  };

  const onDownload = async (item: SharedItem) => {
    try {
      await downloadFile(item.file.id);
    } catch (err) {
      toast.error(extractErrorMessage(err, "다운로드에 실패했습니다."));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <h1 className="text-lg font-semibold">공유됨</h1>
        <p className="mt-0.5 text-sm text-muted">
          내가 속한 그룹을 통해 공유받은 파일과 폴더입니다.
        </p>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<InboxIcon width={40} height={40} />}
            title="공유받은 항목이 없습니다"
            hint="그룹에 초대되고 파일 권한을 받으면 여기에 표시됩니다."
          />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">이름</th>
                  <th className="w-40 px-4 py-2.5 font-medium">그룹</th>
                  <th className="w-24 px-4 py-2.5 font-medium">권한</th>
                  <th className="w-28 px-4 py-2.5 font-medium">크기</th>
                  <th className="w-40 px-4 py-2.5 font-medium">수정일</th>
                  <th className="w-24 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const f = item.file;
                  return (
                    <tr
                      key={`${f.id}-${item.group_id}`}
                      className="group border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)]"
                    >
                      <td className="px-4 py-2.5">
                        <button
                          className="flex items-center gap-2.5 text-left"
                          onClick={() => onOpen(item)}
                          disabled={!f.is_folder}
                        >
                          <span className={f.is_folder ? "text-accent" : "text-muted"}>
                            {f.is_folder ? <FolderIcon /> : <FileIcon />}
                          </span>
                          <span className={`truncate ${f.is_folder ? "font-medium" : ""}`}>
                            {f.name}
                          </span>
                        </button>
                      </td>
                      <td className="px-4 py-2.5 text-muted">
                        <span className="truncate">{item.group_name}</span>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge tone={permissionTone(item.permission)}>
                          {permissionLabel(item.permission)}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-muted">
                        {f.is_folder ? "-" : formatBytes(f.size)}
                      </td>
                      <td className="px-4 py-2.5 text-muted">{formatDateTime(f.updated_at)}</td>
                      <td className="px-4 py-2.5">
                        <div className="flex justify-end opacity-0 transition-opacity group-hover:opacity-100">
                          {!f.is_folder && permissionCovers(item.permission, "read") && (
                            <button
                              title="다운로드"
                              aria-label="다운로드"
                              onClick={() => onDownload(item)}
                              className="rounded-md p-1.5 text-muted transition-colors hover:bg-[color:var(--bg-secondary)] hover:text-[color:var(--text-primary)]"
                            >
                              <DownloadIcon width={16} height={16} />
                            </button>
                          )}
                        </div>
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
  );
}
