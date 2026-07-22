/**
 * 가상 폴더용 파일 목록 뷰 (Phase 8 즐겨찾기·최근). 폴더 탐색/업로드가 없는 읽기 중심 화면이라
 * FileBrowserPage 의 풀 행 액션 대신 다운로드·즐겨찾기만 버튼으로 노출한다. 리스트·그리드를
 * 모두 지원하며 썸네일/포맷 헬퍼는 기존 것을 그대로 재사용한다.
 *
 * 클릭 한 번은 현재 항목 표시, 더블클릭이 "열기"다(터치는 한 번 탭). 미리보기도 이 "열기"에 얹혀 있어
 * 따로 버튼을 두지 않는다 — 파일은 onPreview, 폴더는 onOpenFolder 로 넘겨 호출부가 해당
 * 폴더로 이동시킨다(최근 목록은 파일만 담기므로 실질적으로 즐겨찾기에서만 폴더가 나타난다).
 * 다운로드 버튼은 폴더에도 둔다 — 폴더는 하위 전체가 ZIP 하나로 내려온다.
 */

import { useState } from "react";

import type { FileNode } from "@/api/types";
import { Badge } from "@/components/ui";
import { FavoriteStar } from "@/components/FavoriteStar";
import { Thumbnail } from "@/components/Thumbnail";
import { DownloadIcon, FileIcon, FolderIcon } from "@/components/icons";
import { formatBytes, formatDateTime } from "@/lib/format";
import { permissionLabel, permissionTone } from "@/lib/labels";
import {
  CARD_ACTIVE_CLASS,
  ROW_ACTION_PROPS,
  ROW_ACTIVE_CLASS,
  ROW_BASE_CLASS,
  rowOpenHandlers,
  useCoarsePointer,
} from "@/lib/rowOpen";

export interface FileListViewProps {
  items: FileNode[];
  view?: "list" | "grid";
  onOpenFolder: (f: FileNode) => void;
  onPreview: (f: FileNode) => void;
  onDownload: (f: FileNode) => void;
  onToggleFavorite: (f: FileNode) => void;
}

/**
 * 이 뷰에는 체크박스가 없어 "현재 항목" 하나만 들고 있으면 된다(하이라이트 용도).
 * 상위 화면이 쓰지 않으므로 내부 상태로 둔다.
 */
interface ViewProps extends FileListViewProps {
  activeId: number | null;
  onFocus: (id: number) => void;
  coarse: boolean;
}

/** 행/카드에 그대로 펼치는 현재 항목·열기 핸들러 (파일=미리보기, 폴더=열기). */
function openOf(f: FileNode, p: ViewProps) {
  return rowOpenHandlers({
    coarse: p.coarse,
    focus: () => p.onFocus(f.id),
    open: () => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f)),
  });
}

export function FileListView(props: FileListViewProps) {
  const [activeId, setActiveId] = useState<number | null>(null);
  const coarse = useCoarsePointer();
  const p: ViewProps = { ...props, activeId, onFocus: setActiveId, coarse };
  return (props.view ?? "list") === "grid" ? <Grid {...p} /> : <Table {...p} />;
}

/** 권한 셀 — owner 는 "소유자"로 구분해 강조하고, 그 외는 라벨/톤 배지로 표시. */
function PermissionCell({ f }: { f: FileNode }) {
  if (!f.permission) return <span className="text-muted">-</span>;
  if (f.permission === "owner") {
    return <span className="text-xs font-medium text-accent">소유자</span>;
  }
  return <Badge tone={permissionTone(f.permission)}>{permissionLabel(f.permission)}</Badge>;
}

/** 그룹명 목록을 ", " 로 합쳐 표시하고, 비어 있으면 "-". */
function groupText(f: FileNode): string {
  return f.group_names && f.group_names.length > 0 ? f.group_names.join(", ") : "-";
}

function DownloadButton({ file, onDownload }: { file: FileNode; onDownload: (f: FileNode) => void }) {
  // 폴더는 하위 전체를 ZIP 하나로 묶어 받는다 — 라벨로 그 차이를 알린다.
  const title = file.is_folder ? "ZIP 으로 다운로드" : "다운로드";
  return (
    <button
      title={title}
      aria-label={title}
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

function Table(p: ViewProps) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-token text-left text-xs text-muted">
            <th className="px-4 py-2.5 font-medium">이름</th>
            <th className="w-32 px-4 py-2.5 font-medium">소유자</th>
            <th className="w-40 px-4 py-2.5 font-medium">그룹</th>
            <th className="w-24 px-4 py-2.5 font-medium">권한</th>
            <th className="w-24 px-4 py-2.5 font-medium">크기</th>
            <th className="w-36 px-4 py-2.5 font-medium">수정일</th>
            <th className="w-24 px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {p.items.map((f) => (
            <tr
              key={f.id}
              className={`group border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)] ${ROW_BASE_CLASS} ${
                p.activeId === f.id ? ROW_ACTIVE_CLASS : ""
              }`}
              {...openOf(f, p)}
            >
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2">
                  {/* 클릭 처리는 행이 맡는다 — 이 버튼은 키보드 포커스/Enter 진입점 */}
                  <button
                    className="flex min-w-0 items-center gap-2.5 text-left"
                    title={f.is_folder ? "더블클릭하면 폴더를 엽니다" : "더블클릭하면 미리봅니다"}
                  >
                    <span
                      className={`flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded ${
                        f.is_folder ? "text-accent" : "text-muted"
                      }`}
                    >
                      <Thumbnail
                        file={f}
                        className="h-8 w-8 rounded object-cover"
                        fallback={f.is_folder ? <FolderIcon /> : <FileIcon />}
                      />
                    </span>
                    <span className="flex min-w-0 flex-col">
                      <span className="flex items-center gap-1.5">
                        <span className={`truncate ${f.is_folder ? "font-medium" : ""}`}>{f.name}</span>
                        {!f.is_folder && f.current_version >= 2 && (
                          <Badge tone="neutral">v{f.current_version}</Badge>
                        )}
                      </span>
                      {f.location && (
                        <span className="truncate text-xs text-muted">{f.location}</span>
                      )}
                    </span>
                  </button>
                  <span {...ROW_ACTION_PROPS}>
                    <FavoriteStar active={f.is_favorite} onToggle={() => p.onToggleFavorite(f)} />
                  </span>
                </div>
              </td>
              <td className="px-4 py-2.5 text-muted">
                <span className="block truncate" title={f.owner_name ?? undefined}>
                  {f.owner_name ?? "-"}
                </span>
              </td>
              <td className="px-4 py-2.5 text-muted">
                <span className="block truncate" title={groupText(f)}>
                  {groupText(f)}
                </span>
              </td>
              <td className="px-4 py-2.5">
                <PermissionCell f={f} />
              </td>
              <td className="px-4 py-2.5 text-muted">{f.is_folder ? "-" : formatBytes(f.size)}</td>
              <td className="px-4 py-2.5 text-muted">{formatDateTime(f.updated_at)}</td>
              <td className="px-4 py-2.5">
                <div
                  className="flex justify-end opacity-0 transition-opacity group-hover:opacity-100"
                  {...ROW_ACTION_PROPS}
                >
                  <DownloadButton file={f} onDownload={p.onDownload} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Grid(p: ViewProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
      {p.items.map((f) => (
        <div
          key={f.id}
          className={`group card relative flex flex-col overflow-hidden p-0 ${ROW_BASE_CLASS} ${
            p.activeId === f.id ? CARD_ACTIVE_CLASS : ""
          }`}
          {...openOf(f, p)}
        >
          {/* 항상 표시되는 즐겨찾기 별(비활성은 hover 노출) */}
          <span className="absolute right-1.5 top-1.5 z-10" {...ROW_ACTION_PROPS}>
            <FavoriteStar active={f.is_favorite} onToggle={() => p.onToggleFavorite(f)} />
          </span>
          <button
            className="relative flex aspect-square w-full items-center justify-center overflow-hidden bg-muted-token"
            title={f.is_folder ? "더블클릭하면 폴더를 엽니다" : "더블클릭하면 미리봅니다"}
          >
            <span
              className={`flex items-center justify-center ${
                f.is_folder ? "text-accent" : "text-muted"
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
            {/* 그리드는 간결 유지 — 소유자가 아닌 항목만 작은 권한 배지 노출 */}
            {f.permission && f.permission !== "owner" && (
              <span className="absolute bottom-1.5 left-1.5">
                <Badge tone={permissionTone(f.permission)}>{permissionLabel(f.permission)}</Badge>
              </span>
            )}
          </button>

          <div className="flex items-center gap-1 border-t border-token px-2.5 py-2">
            <button className="min-w-0 flex-1 text-left">
              <p className={`truncate text-xs ${f.is_folder ? "font-medium" : ""}`}>{f.name}</p>
              <p className="text-[10px] text-muted">{f.is_folder ? "폴더" : formatBytes(f.size)}</p>
              {f.location && <p className="truncate text-[10px] text-muted">{f.location}</p>}
            </button>
            <span
              className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
              {...ROW_ACTION_PROPS}
            >
              <DownloadButton file={f} onDownload={p.onDownload} />
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
