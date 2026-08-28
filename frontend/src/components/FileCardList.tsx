/**
 * 좁은 화면(휴대폰)용 파일 목록 — 표 대신 한 줄에 한 항목씩 쌓는 카드 목록.
 *
 * 데스크톱 표는 여덟 열(이름·소유자·그룹·권한·크기·수정일·액션)이라 폭이 1000px 을 넘는데,
 * 390px 화면에서는 컨테이너가 340px 뿐이라 이름과 소유자 앞 글자만 보이고 나머지는 화면
 * 밖으로 밀린다. 옆으로 밀어서 볼 수는 있었지만, 목록을 훑는 동작이 "한 행마다 좌우로
 * 왕복"이 되어 버린다.
 *
 * 그래서 모바일에서는 열을 버리고 **한 항목이 한 덩어리**가 되게 한다. 이름을 첫 줄에,
 * 사람이 실제로 찾는 보조 정보(크기·수정일)를 둘째 줄에 두고, 나머지 열(소유자·그룹·권한)은
 * 필요할 때 데스크톱에서 본다. 아홉 개까지 늘어나는 행 액션은 `⋯` 하나로 접어 하단 시트로
 * 보낸다 — 라벨 없는 28px 아이콘 아홉 개는 손가락으로 고를 수 있는 물건이 아니다.
 *
 * 열기 판정은 표와 같은 `rowOpenHandlers` 를 쓴다. 목록의 생김새가 달라도 "탭하면 열린다"는
 * 규칙까지 갈라지면 안 된다.
 */

import { useState } from "react";

import type { FileNode } from "@/api/types";
import { ActionSheet, type SheetAction } from "@/components/ActionSheet";
import { FavoriteStar } from "@/components/FavoriteStar";
import { Thumbnail } from "@/components/Thumbnail";
import { FileIcon, FolderIcon, MoreIcon } from "@/components/icons";
import { formatBytes, formatDateTime } from "@/lib/format";
import {
  ROW_ACTION_PROPS,
  ROW_ACTIVE_CLASS,
  ROW_BASE_CLASS,
  ROW_SELECTED_CLASS,
  rowOpenHandlers,
  useCoarsePointer,
} from "@/lib/rowOpen";

export interface FileCardListProps {
  items: FileNode[];
  /** 항목별 동작 목록. 호출부가 권한 판정을 마친 뒤 넘긴다(데스크톱 아이콘 줄과 같은 출처). */
  actionsFor: (f: FileNode) => SheetAction[];
  onOpenFolder: (f: FileNode) => void;
  onPreview: (f: FileNode) => void;
  onToggleFavorite: (f: FileNode) => void;
  /** 이름 앞에 붙일 표식(위키 마크·버전 배지 등). 화면마다 다르므로 주입받는다. */
  decorate?: (f: FileNode) => React.ReactNode;
  // --- 현재 항목 / 체크 선택 — 표와 같은 개념. 선택을 안 쓰는 화면은 통째로 생략한다. ---
  activeId?: number | null;
  onFocus?: (id: number) => void;
  selectedIds?: number[];
  onSelect?: (id: number) => void;
  /** 목록 맨 위에 고정할 항목(내 드라이브 루트의 "공유" 가상 폴더 등). */
  leading?: React.ReactNode;
}

export function FileCardList({
  items,
  actionsFor,
  onOpenFolder,
  onPreview,
  onToggleFavorite,
  decorate,
  activeId = null,
  onFocus,
  selectedIds,
  onSelect,
  leading,
}: FileCardListProps) {
  const coarse = useCoarsePointer();
  // 시트를 연 대상. 항목 하나만 열리므로 배열이 아니라 단일 값으로 든다.
  const [sheetFor, setSheetFor] = useState<FileNode | null>(null);

  return (
    <div className="card divide-y divide-token overflow-hidden">
      {leading}
      {items.map((f) => {
        const selected = selectedIds?.includes(f.id) ?? false;
        return (
          <div
            key={f.id}
            className={`flex items-center gap-3 px-3 py-2.5 ${ROW_BASE_CLASS} ${
              selected ? ROW_SELECTED_CLASS : activeId === f.id ? ROW_ACTIVE_CLASS : ""
            }`}
            {...rowOpenHandlers({
              coarse,
              focus: () => onFocus?.(f.id),
              select: onSelect ? () => onSelect(f.id) : undefined,
              open: () => (f.is_folder ? onOpenFolder(f) : onPreview(f)),
            })}
          >
            {onSelect && (
              <span {...ROW_ACTION_PROPS} className="shrink-0">
                <input
                  type="checkbox"
                  checked={selected}
                  aria-label={`${f.name} 선택`}
                  onChange={() => onSelect(f.id)}
                />
              </span>
            )}

            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded ${
                f.is_folder ? "text-accent" : "text-muted"
              }`}
            >
              <Thumbnail
                file={f}
                className="h-9 w-9 rounded object-cover"
                fallback={f.is_folder ? <FolderIcon /> : <FileIcon />}
              />
            </span>

            <div className="min-w-0 flex-1">
              <p className={`truncate text-sm ${f.is_folder ? "font-medium" : ""}`}>
                {decorate?.(f)}
                {f.name}
              </p>
              <p className="truncate text-xs text-muted">
                {f.is_folder ? "폴더" : formatBytes(f.size)} · {formatDateTime(f.updated_at)}
              </p>
            </div>

            {/*
              별과 ⋯ 는 "행을 고르는 뜻"이 아니므로 클릭 판정에서 뺀다. 모바일은 한 번 탭이
              곧 열기라, 빼지 않으면 별을 누를 때마다 파일이 함께 열린다.
            */}
            <span {...ROW_ACTION_PROPS} className="flex shrink-0 items-center">
              <FavoriteStar active={f.is_favorite} onToggle={() => onToggleFavorite(f)} />
              <button
                className="icon-btn p-2 text-muted"
                aria-label={`${f.name} 작업 더 보기`}
                onClick={() => setSheetFor(f)}
              >
                <MoreIcon width={18} height={18} />
              </button>
            </span>
          </div>
        );
      })}

      <ActionSheet
        open={sheetFor !== null}
        title={sheetFor?.name ?? ""}
        actions={sheetFor ? actionsFor(sheetFor) : []}
        onClose={() => setSheetFor(null)}
      />
    </div>
  );
}
