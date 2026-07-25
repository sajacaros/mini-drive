import { useCallback, useEffect, useRef, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { listTrash, permanentDeleteFile, restoreFile } from "@/api/files";
import type { FileNode } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { FileIcon, FolderIcon, RestoreIcon, TrashIcon } from "@/components/icons";
import { subscribeFileEvents } from "@/lib/fileEvents";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/store/auth";

const DAY_MS = 24 * 60 * 60 * 1000;

/** 시각을 그 날의 0시로 내린 밀리초. 남은 기간을 시분 차이가 아닌 날짜 차이로 세기 위함. */
function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** 두 시각 사이의 달력 일수 차이. "3.4일 남음"이 아니라 "3일 후"로 읽히게 한다. */
function daysUntil(iso: string, now: Date): number | null {
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return null;
  return Math.round((startOfDay(target) - startOfDay(now)) / DAY_MS);
}

/**
 * 자동 삭제 예정 표시. 정리는 하루 1회 정해진 시각에 돌므로 예정일이 지나도 다음 회차까지는
 * 남아 있다 — 그 상태를 "오늘"로 뭉개지 않고 "곧"으로 구분해 보여준다.
 */
function purgeLabel(days: number): string {
  if (days > 0) return `${days}일 후 삭제`;
  if (days === 0) return "오늘 삭제";
  return "곧 삭제";
}

/** 자동 삭제 열. 임박(3일 이하)하면 경고색을 주고, 정확한 시각은 툴팁으로 넘긴다. */
function renderPurgeCell(f: FileNode, now: Date) {
  const days = f.purge_at ? daysUntil(f.purge_at, now) : null;
  // 자동 정리가 꺼져 있으면(purge_at 없음) 표시할 예정일이 없다.
  if (days === null) return <span className="text-muted">—</span>;
  return (
    <span className={days <= 3 ? "text-warning" : "text-muted"} title={formatDateTime(f.purge_at)}>
      {purgeLabel(days)}
    </span>
  );
}

export function TrashPage() {
  const toast = useToast();
  const refreshUser = useAuthStore((s) => s.refreshUser);

  const [items, setItems] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<FileNode | null>(null);

  /**
   * silent 는 SSE 로 촉발된 배경 갱신용 — 스피너로 표를 갈아엎지 않고, 실패해도 기존 목록을
   * 지우지 않는다(다음 이벤트나 재연결 시 다시 맞춰진다).
   */
  const load = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = opts?.silent ?? false;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      setItems(await listTrash());
    } catch (err) {
      if (!silent) setError(extractErrorMessage(err, "휴지통을 불러오지 못했습니다."));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // --- 실시간 반영 (spec/trash-retention-purge.md) --------------------------
  // 다른 탭에서 버리거나 복구한 항목, 그리고 보존 기간이 지나 자동 영구 삭제된 항목을 반영한다.
  // 자동 정리는 한 회차에 수백 건을 지울 수 있으므로 이벤트마다 재조회하지 않는다 — 개별
  // purge 는 해당 행만 제거하고, 재조회가 필요한 이벤트만 300ms 로 병합한다.
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const needsReload = useRef(false);

  const scheduleRefresh = useCallback(
    (reload: boolean) => {
      if (reload) needsReload.current = true;
      if (reloadTimer.current) clearTimeout(reloadTimer.current);
      reloadTimer.current = setTimeout(() => {
        const full = needsReload.current;
        needsReload.current = false;
        if (full) void load({ silent: true });
        // 영구 삭제·복구는 할당량을 움직이므로 사이드바 사용량도 함께 맞춘다.
        void refreshUser();
      }, 300);
    },
    [load, refreshUser],
  );

  useEffect(() => {
    const unsub = subscribeFileEvents({
      onEvent: (e) => {
        if (e.type === "purge") {
          // 요약 이벤트(file_id 없음)는 몇 건이 사라졌는지만 알려주므로 목록을 다시 받는다.
          if (typeof e.file_id !== "number") {
            scheduleRefresh(true);
            return;
          }
          const id = e.file_id;
          setItems((prev) => prev.filter((f) => f.id !== id));
          scheduleRefresh(false);
          return;
        }
        // 소프트 삭제(항목 추가)·복구(항목 제거)는 목록을 다시 받아야 알 수 있다.
        if (e.type === "delete" || e.type === "restore") scheduleRefresh(true);
      },
      // 끊긴 동안 놓친 변경 보정.
      onReconnect: () => scheduleRefresh(true),
    });
    return () => {
      unsub();
      if (reloadTimer.current) clearTimeout(reloadTimer.current);
    };
  }, [scheduleRefresh]);

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

  const now = new Date();
  // 보존 기간은 응답에 따로 담기지 않는다 — 아무 행에서나 purge_at - deleted_at 으로 역산한다.
  // 자동 정리가 꺼져 있으면(purge_at 이 전부 null) null 이 되어 안내 문구가 숨겨진다.
  const sample = items.find((f) => f.purge_at && f.deleted_at);
  const retentionDays =
    sample?.purge_at && sample.deleted_at
      ? Math.round(
          (new Date(sample.purge_at).getTime() - new Date(sample.deleted_at).getTime()) / DAY_MS,
        )
      : null;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
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
          <div className="card overflow-x-auto">
            {retentionDays !== null && (
              <p className="border-b border-token px-4 py-2.5 text-xs text-muted">
                휴지통 항목은 삭제 후 {retentionDays}일이 지나면 자동으로 영구 삭제됩니다.
              </p>
            )}
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">이름</th>
                  <th className="w-28 px-4 py-2.5 font-medium">크기</th>
                  <th className="w-40 px-4 py-2.5 font-medium">삭제일</th>
                  <th className="w-32 px-4 py-2.5 font-medium">자동 삭제</th>
                  <th className="w-40 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {items.map((f) => (
                  <tr key={f.id} className="border-b border-token last:border-0">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <span className={f.is_folder ? "text-accent" : "text-muted"}>
                          {f.is_folder ? <FolderIcon /> : <FileIcon />}
                        </span>
                        <span className="truncate">{f.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {f.is_folder ? "-" : formatBytes(f.size)}
                    </td>
                    <td className="px-4 py-2.5 text-muted">{formatDateTime(f.deleted_at)}</td>
                    <td className="px-4 py-2.5">{renderPurgeCell(f, now)}</td>
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
