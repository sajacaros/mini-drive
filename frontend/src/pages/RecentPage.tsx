/**
 * 최근 이용 항목 (Phase 8-3). listRecent(50) 을 최신순으로 보여준다. 미리보기·다운로드 시
 * 서버가 기록하므로 목록에는 파일만 담긴다(폴더 열람은 기록하지 않음). 별 토글은 등록/해제를
 * 오갈 뿐 목록에서 항목을 빼지 않는다(즐겨찾기 뷰와 달리 최근 이력 자체는 유지).
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { addFavorite, listRecent, removeFavorite } from "@/api/files";
import type { FileNode } from "@/api/types";
import { FileListView } from "@/components/FileListView";
import { PageHeader } from "@/components/PageHeader";
import { PreviewModal } from "@/components/PreviewModal";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { HistoryIcon } from "@/components/icons";
import { downloadFile } from "@/lib/download";
import { fetchFilePreview } from "@/lib/preview";

const RECENT_LIMIT = 50;

export function RecentPage() {
  const toast = useToast();

  const [items, setItems] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [previewTarget, setPreviewTarget] = useState<FileNode | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listRecent(RECENT_LIMIT));
    } catch (err) {
      setError(extractErrorMessage(err, "최근 항목을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 최근 뷰의 별 토글은 등록/해제만 반영(항목은 유지). 낙관적 flip, 실패 시 되돌린다.
  const onToggleFavorite = async (f: FileNode) => {
    const next = !f.is_favorite;
    setItems((list) => list.map((it) => (it.id === f.id ? { ...it, is_favorite: next } : it)));
    try {
      await (next ? addFavorite(f.id) : removeFavorite(f.id));
    } catch (err) {
      setItems((list) =>
        list.map((it) => (it.id === f.id ? { ...it, is_favorite: f.is_favorite } : it)),
      );
      toast.error(extractErrorMessage(err, "즐겨찾기 변경에 실패했습니다."));
    }
  };

  const onDownload = async (f: FileNode) => {
    try {
      await downloadFile(f.id);
    } catch (err) {
      toast.error(extractErrorMessage(err, "다운로드에 실패했습니다."));
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
        <h1 className="flex items-center gap-2 text-lg font-semibold">
          <span className="text-[color:var(--accent)]">
            <HistoryIcon width={18} height={18} />
          </span>
          최근
        </h1>
        <p className="mt-0.5 text-sm text-muted">최근에 미리보거나 내려받은 파일입니다.</p>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<HistoryIcon width={40} height={40} />}
            title="최근 이용한 항목이 없습니다"
            hint="파일을 미리보거나 내려받으면 여기에 표시됩니다."
          />
        ) : (
          <FileListView
            items={items}
            view="list"
            onOpenFolder={() => {}}
            onPreview={setPreviewTarget}
            onDownload={onDownload}
            onToggleFavorite={onToggleFavorite}
          />
        )}
      </div>

      <PreviewModal
        open={previewTarget !== null}
        title={previewTarget?.name ?? ""}
        onClose={() => setPreviewTarget(null)}
        load={() => fetchFilePreview(previewTarget!.id)}
        onDownload={previewTarget ? () => void onDownload(previewTarget) : undefined}
      />
    </div>
  );
}
