/**
 * 즐겨찾기 가상 폴더 (Phase 8-2). listFavorites 데이터를 FileListView 로 렌더한다.
 * 폴더는 클릭 시 공유 폴더 브라우저(/shared/f/:id — 소유/공유 무관하게 권한 재확인 후 진입)로
 * 이동하고, 파일은 미리보기한다. 별을 해제하면 목록에서 즉시 빠진다(낙관적, 실패 시 복원).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import { listFavorites, removeFavorite } from "@/api/files";
import type { FileNode } from "@/api/types";
import { FileListView } from "@/components/FileListView";
import { PreviewModal } from "@/components/PreviewModal";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, LoadingState, Pagination } from "@/components/ui";
import { GridIcon, ListIcon, StarIcon } from "@/components/icons";
import { downloadFile } from "@/lib/download";
import { fetchFilePreview } from "@/lib/preview";

const PAGE_SIZE = 50;
const VIEW_KEY = "minidrive:fileView";
type ViewMode = "list" | "grid";

export function FavoritesPage() {
  const toast = useToast();
  const navigate = useNavigate();

  const [items, setItems] = useState<FileNode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [previewTarget, setPreviewTarget] = useState<FileNode | null>(null);
  const [view, setView] = useState<ViewMode>(
    () => (localStorage.getItem(VIEW_KEY) as ViewMode) || "list",
  );

  const changeView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };

  const load = useCallback(async (pageNum: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listFavorites(pageNum, PAGE_SIZE);
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "즐겨찾기를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(page);
  }, [load, page]);

  // 즐겨찾기 뷰에서 별을 누르면 해제 — 낙관적으로 목록에서 빼고 실패 시 되돌린다.
  const onToggleFavorite = async (f: FileNode) => {
    const prev = items;
    setItems((list) => list.filter((it) => it.id !== f.id));
    setTotal((t) => Math.max(0, t - 1));
    try {
      await removeFavorite(f.id);
    } catch (err) {
      setItems(prev);
      setTotal((t) => t + 1);
      toast.error(extractErrorMessage(err, "즐겨찾기 해제에 실패했습니다."));
    }
  };

  const onDownload = async (f: FileNode) => {
    try {
      await downloadFile(f.id);
    } catch (err) {
      toast.error(extractErrorMessage(err, "다운로드에 실패했습니다."));
    }
  };

  const openFolder = (f: FileNode) =>
    navigate(`/shared/f/${f.id}`, { state: { name: f.name } });

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen flex-col">
      <div className="flex items-center justify-between gap-4 border-b border-token px-6 py-4">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <span className="text-[color:var(--accent)]">
              <StarIcon width={18} height={18} filled />
            </span>
            즐겨찾기
          </h1>
          <p className="mt-0.5 text-sm text-muted">별표한 파일과 폴더입니다.</p>
        </div>

        <div className="flex items-center rounded-lg border border-token p-0.5">
          <button
            className={`rounded-md p-1.5 transition-colors ${
              view === "list"
                ? "bg-[color:var(--bg-muted)] text-[color:var(--text-primary)]"
                : "text-muted hover:text-[color:var(--text-primary)]"
            }`}
            title="목록 보기"
            aria-label="목록 보기"
            aria-pressed={view === "list"}
            onClick={() => changeView("list")}
          >
            <ListIcon width={16} height={16} />
          </button>
          <button
            className={`rounded-md p-1.5 transition-colors ${
              view === "grid"
                ? "bg-[color:var(--bg-muted)] text-[color:var(--text-primary)]"
                : "text-muted hover:text-[color:var(--text-primary)]"
            }`}
            title="그리드 보기"
            aria-label="그리드 보기"
            aria-pressed={view === "grid"}
            onClick={() => changeView("grid")}
          >
            <GridIcon width={16} height={16} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load(page)} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<StarIcon width={40} height={40} />}
            title="즐겨찾기한 항목이 없습니다"
            hint="파일이나 폴더의 별 아이콘을 눌러 즐겨찾기에 추가하세요."
          />
        ) : (
          <>
            <FileListView
              items={items}
              view={view}
              onOpenFolder={openFolder}
              onPreview={setPreviewTarget}
              onDownload={onDownload}
              onToggleFavorite={onToggleFavorite}
            />
            <Pagination page={page} totalPages={totalPages} onChange={setPage} />
          </>
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
