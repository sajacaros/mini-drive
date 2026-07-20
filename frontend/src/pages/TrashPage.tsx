import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { listTrash, permanentDeleteFile, restoreFile } from "@/api/files";
import type { FileNode } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { FileIcon, FolderIcon, RestoreIcon, TrashIcon } from "@/components/icons";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/store/auth";

export function TrashPage() {
  const toast = useToast();
  const refreshUser = useAuthStore((s) => s.refreshUser);

  const [items, setItems] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<FileNode | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listTrash());
    } catch (err) {
      setError(extractErrorMessage(err, "휴지통을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onRestore = async (file: FileNode) => {
    try {
      await restoreFile(file.id);
      toast.success("복구했습니다.");
      await load();
      await refreshUser();
    } catch (err) {
      toast.error(extractErrorMessage(err, "복구에 실패했습니다."));
    }
  };

  const confirmPurge = async () => {
    if (!purgeTarget) return;
    try {
      await permanentDeleteFile(purgeTarget.id);
      toast.success("영구 삭제했습니다.");
      setPurgeTarget(null);
      await load();
      await refreshUser();
    } catch (err) {
      toast.error(extractErrorMessage(err, "영구 삭제에 실패했습니다."));
      setPurgeTarget(null);
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <h1 className="text-lg font-semibold">휴지통</h1>
          <p className="mt-0.5 text-sm text-muted">삭제한 항목을 복구하거나 영구 삭제할 수 있습니다.</p>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : items.length === 0 ? (
          <EmptyState icon={<TrashIcon width={40} height={40} />} title="휴지통이 비어 있습니다" />
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">이름</th>
                  <th className="w-28 px-4 py-2.5 font-medium">크기</th>
                  <th className="w-40 px-4 py-2.5 font-medium">삭제일</th>
                  <th className="w-40 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {items.map((f) => (
                  <tr key={f.id} className="border-b border-token last:border-0">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <span className={f.is_folder ? "text-[color:var(--accent)]" : "text-muted"}>
                          {f.is_folder ? <FolderIcon /> : <FileIcon />}
                        </span>
                        <span className="truncate">{f.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {f.is_folder ? "-" : formatBytes(f.size)}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{formatDateTime(f.deleted_at)}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex justify-end gap-2">
                        <button className="btn btn-secondary" onClick={() => onRestore(f)}>
                          <RestoreIcon width={16} height={16} />
                          복구
                        </button>
                        <button className="btn btn-ghost text-danger" onClick={() => setPurgeTarget(f)}>
                          영구 삭제
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        open={purgeTarget !== null}
        title="영구 삭제"
        onClose={() => setPurgeTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setPurgeTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmPurge}>
              영구 삭제
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{purgeTarget?.name}</span> 을(를) 영구 삭제합니다. 이
          작업은 되돌릴 수 없습니다.
        </p>
      </Modal>
    </div>
  );
}
