/**
 * 가상 폴더용 파일 목록 뷰 (Phase 8 즐겨찾기·최근). 폴더 탐색/업로드가 없는 읽기 중심 화면이라
 * FileBrowserPage 의 풀 행 액션 대신 미리보기·다운로드·즐겨찾기만 노출한다. 리스트·그리드를
 * 모두 지원하며 썸네일/포맷 헬퍼는 기존 것을 그대로 재사용한다.
 *
 * 폴더 항목은 클릭 시 onOpenFolder 로 넘겨 호출부가 해당 폴더로 이동시킨다(최근 목록은 파일만
 * 담기므로 실질적으로 즐겨찾기에서만 폴더가 나타난다).
 */

import type { FileNode } from "@/api/types";
import { Badge } from "@/components/ui";
import { FavoriteStar } from "@/components/FavoriteStar";
import { Thumbnail } from "@/components/Thumbnail";
import { DownloadIcon, FileIcon, FolderIcon } from "@/components/icons";
import { formatBytes, formatDateTime } from "@/lib/format";

export interface FileListViewProps {
  items: FileNode[];
  view?: "list" | "grid";
  onOpenFolder: (f: FileNode) => void;
  onPreview: (f: FileNode) => void;
  onDownload: (f: FileNode) => void;
  onToggleFavorite: (f: FileNode) => void;
}

/** 파일=미리보기, 폴더=열기 로 분기하는 공통 클릭 핸들러. */
function openOf(f: FileNode, p: FileListViewProps) {
  return () => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f));
}

export function FileListView(props: FileListViewProps) {
  return (props.view ?? "list") === "grid" ? <Grid {...props} /> : <Table {...props} />;
}

function DownloadButton({ file, onDownload }: { file: FileNode; onDownload: (f: FileNode) => void }) {
  return (
    <button
      title="다운로드"
      aria-label="다운로드"
      onClick={(e) => {
        e.stopPropagation();
        onDownload(file);
      }}
      className="rounded-md p-1.5 text-muted transition-colors hover:bg-[color:var(--bg-secondary)] hover:text-[color:var(--text-primary)]"
    >
      <DownloadIcon width={16} height={16} />
    </button>
  );
}

function Table(p: FileListViewProps) {
  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-token text-left text-xs text-muted">
            <th className="px-4 py-2.5 font-medium">이름</th>
            <th className="w-28 px-4 py-2.5 font-medium">크기</th>
            <th className="w-40 px-4 py-2.5 font-medium">수정일</th>
            <th className="w-32 px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {p.items.map((f) => (
            <tr
              key={f.id}
              className="group border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)]"
            >
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <button className="flex min-w-0 items-center gap-2.5 text-left" onClick={openOf(f, p)}>
                    <span
                      className={`flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded ${
                        f.is_folder ? "text-[color:var(--accent)]" : "text-muted"
                      }`}
                    >
                      <Thumbnail
                        file={f}
                        className="h-8 w-8 rounded object-cover"
                        fallback={f.is_folder ? <FolderIcon /> : <FileIcon />}
                      />
                    </span>
                    <span className={`truncate ${f.is_folder ? "font-medium" : ""}`}>{f.name}</span>
                    {!f.is_folder && f.current_version >= 2 && (
                      <Badge tone="neutral">v{f.current_version}</Badge>
                    )}
                  </button>
                  <FavoriteStar active={f.is_favorite} onToggle={() => p.onToggleFavorite(f)} />
                </div>
              </td>
              <td className="px-4 py-2.5 text-muted">{f.is_folder ? "-" : formatBytes(f.size)}</td>
              <td className="px-4 py-2.5 text-muted">{formatDateTime(f.updated_at)}</td>
              <td className="px-4 py-2.5">
                <div className="flex justify-end opacity-0 transition-opacity group-hover:opacity-100">
                  {!f.is_folder && <DownloadButton file={f} onDownload={p.onDownload} />}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Grid(p: FileListViewProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
      {p.items.map((f) => (
        <div key={f.id} className="group card relative flex flex-col overflow-hidden p-0">
          {/* 항상 표시되는 즐겨찾기 별(비활성은 hover 노출) */}
          <span className="absolute right-1.5 top-1.5 z-10">
            <FavoriteStar active={f.is_favorite} onToggle={() => p.onToggleFavorite(f)} />
          </span>
          <button
            className="relative flex aspect-square w-full items-center justify-center overflow-hidden bg-muted-token"
            onClick={openOf(f, p)}
            title={f.is_folder ? "폴더 열기" : "미리보기"}
          >
            <span
              className={`flex items-center justify-center ${
                f.is_folder ? "text-[color:var(--accent)]" : "text-muted"
              }`}
            >
              <Thumbnail
                file={f}
                className="h-full w-full object-cover"
                fallback={
                  f.is_folder ? <FolderIcon width={44} height={44} /> : <FileIcon width={40} height={40} />
                }
              />
            </span>
            {!f.is_folder && f.current_version >= 2 && (
              <span className="absolute left-1.5 top-1.5">
                <Badge tone="neutral">v{f.current_version}</Badge>
              </span>
            )}
          </button>

          <div className="flex items-center gap-1 border-t border-token px-2.5 py-2">
            <button className="min-w-0 flex-1 text-left" onClick={openOf(f, p)}>
              <p className={`truncate text-xs ${f.is_folder ? "font-medium" : ""}`}>{f.name}</p>
              <p className="text-[10px] text-muted">{f.is_folder ? "폴더" : formatBytes(f.size)}</p>
            </button>
            {!f.is_folder && (
              <span className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100">
                <DownloadButton file={f} onDownload={p.onDownload} />
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
