/**
 * 버전 히스토리 모달 (PRD 3.3).
 *
 * 파일별 버전 목록(버전 번호·크기·업로더·일시·현재 배지)을 보여주고, 각 버전을 티켓으로
 * 다운로드하거나 과거 버전을 새 버전으로 복구한다. 복구는 확인 다이얼로그를 거치며, 성공 시
 * 목록을 갱신하고 부모에 변경을 알린다(파일 목록의 size/updated_at/current_version 반영용).
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { listVersions, restoreVersion } from "@/api/files";
import type { FileNode, FileVersion } from "@/api/types";
import { downloadFileVersion } from "@/lib/download";
import { formatBytes, formatDateTime } from "@/lib/format";
import { Modal } from "./Modal";
import { useToast } from "./Toast";
import { Badge, ErrorState, LoadingState, Spinner } from "./ui";
import { DownloadIcon, RestoreIcon } from "./icons";

export function VersionHistoryModal({
  file,
  open,
  onClose,
  onChanged,
}: {
  file: FileNode | null;
  open: boolean;
  onClose: () => void;
  /** 복구 등으로 파일이 바뀌었을 때 호출 — 부모가 파일 목록을 갱신한다. */
  onChanged?: () => void;
}) {
  const toast = useToast();
  const [items, setItems] = useState<FileVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyVersion, setBusyVersion] = useState<number | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<FileVersion | null>(null);
  const [restoring, setRestoring] = useState(false);

  const fileId = file?.id ?? null;

  const load = useCallback(async () => {
    if (fileId == null) return;
    setLoading(true);
    setError(null);
    try {
      const res = await listVersions(fileId);
      setItems(res.items);
    } catch (err) {
      setError(extractErrorMessage(err, "버전 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [fileId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const onDownload = async (version: number) => {
    if (fileId == null) return;
    setBusyVersion(version);
    try {
      await downloadFileVersion(fileId, version);
    } catch (err) {
      toast.error(extractErrorMessage(err, "다운로드에 실패했습니다."));
    } finally {
      setBusyVersion(null);
    }
  };

  const confirmRestore = async () => {
    if (fileId == null || !restoreTarget) return;
    setRestoring(true);
    try {
      await restoreVersion(fileId, restoreTarget.version);
      toast.success(`v${restoreTarget.version}을(를) 새 버전으로 복구했습니다.`);
      setRestoreTarget(null);
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(extractErrorMessage(err, "버전 복구에 실패했습니다."));
    } finally {
      setRestoring(false);
    }
  };

  return (
    <>
      <Modal
        open={open}
        title="버전 기록"
        onClose={onClose}
        footer={
          <button className="btn btn-secondary" onClick={onClose}>
            닫기
          </button>
        }
      >
        {file && (
          <p className="mb-3 truncate text-sm text-muted">
            대상 파일: <span className="font-medium text-[color:var(--text-primary)]">{file.name}</span>
          </p>
        )}

        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : items.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted">버전 기록이 없습니다.</p>
        ) : (
          <ul className="flex max-h-[60vh] flex-col gap-2 overflow-auto">
            {items.map((v) => (
              <li
                key={v.version}
                className="flex items-center justify-between gap-3 rounded-lg border border-token px-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">v{v.version}</span>
                    {v.is_current && <Badge tone="accent">현재 버전</Badge>}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted">
                    {formatBytes(v.size)} · {v.uploaded_by_name} · {formatDateTime(v.uploaded_at)}
                  </p>
                </div>
                <div className="flex shrink-0 gap-0.5">
                  <button
                    title="이 버전 다운로드"
                    aria-label={`v${v.version} 다운로드`}
                    onClick={() => onDownload(v.version)}
                    disabled={busyVersion === v.version}
                    className="rounded-md p-1.5 text-muted transition-colors hover:bg-[color:var(--bg-secondary)] hover:text-[color:var(--text-primary)] disabled:opacity-50"
                  >
                    {busyVersion === v.version ? (
                      <Spinner className="h-4 w-4" />
                    ) : (
                      <DownloadIcon width={16} height={16} />
                    )}
                  </button>
                  {!v.is_current && (
                    <button
                      title="이 버전으로 복구"
                      aria-label={`v${v.version} 복구`}
                      onClick={() => setRestoreTarget(v)}
                      className="rounded-md p-1.5 text-muted transition-colors hover:bg-[color:var(--bg-secondary)] hover:text-[color:var(--text-primary)]"
                    >
                      <RestoreIcon width={16} height={16} />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Modal>

      {/* 복구 확인 */}
      <Modal
        open={restoreTarget !== null}
        title="버전 복구"
        onClose={() => !restoring && setRestoreTarget(null)}
        footer={
          <>
            <button
              className="btn btn-secondary"
              onClick={() => setRestoreTarget(null)}
              disabled={restoring}
            >
              취소
            </button>
            <button className="btn btn-primary" onClick={confirmRestore} disabled={restoring}>
              {restoring ? <Spinner className="h-4 w-4" /> : "복구"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">v{restoreTarget?.version}</span>을(를) 새 버전으로
          복구합니다. 기존 이력은 보존됩니다.
        </p>
      </Modal>
    </>
  );
}
