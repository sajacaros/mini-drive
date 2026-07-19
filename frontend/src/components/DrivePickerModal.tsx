/**
 * 드라이브 파일/폴더 선택 모달 (위키 소스 등록에 재사용).
 *
 * 전용 파일 선택 컴포넌트가 없어(FileBrowserPage 는 페이지 전체) 여기서 간단한 폴더 탐색기를
 * 제공한다. 폴더는 열어 들어가거나 소스로 선택할 수 있고, 파일도 선택할 수 있다. 폴더 공유는
 * 항상 하위의 내 소유 파일까지 재귀 컴파일되므로(wiki-v2 D2) 재귀 옵션은 없다.
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { listFiles } from "@/api/files";
import type { FileNode } from "@/api/types";
import { FileIcon, FolderIcon } from "./icons";
import { ErrorState, LoadingState } from "./ui";

interface Crumb {
  id: number | null;
  name: string;
}

interface DrivePickerModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  /** 선택 확정. */
  onPick: (node: FileNode) => void;
  /** 이미 등록된 파일 id — 목록에서 "등록됨" 으로 비활성화한다. */
  disabledIds?: Set<number>;
  /** 지정 시 이 사용자 소유 항목만 선택 가능(그 외는 "내 소유 아님" 표시). 폴더 탐색은 허용. */
  pickableOwnerId?: number;
}

const PAGE_SIZE = 100;

export function DrivePickerModal({
  open,
  title,
  onClose,
  onPick,
  disabledIds,
  pickableOwnerId,
}: DrivePickerModalProps) {
  const [path, setPath] = useState<Crumb[]>([{ id: null, name: "내 드라이브" }]);
  const [items, setItems] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const current = path[path.length - 1];

  const load = useCallback(async (parentId: number | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listFiles(parentId, 1, PAGE_SIZE);
      setItems(res.items);
    } catch (err) {
      setError(extractErrorMessage(err, "목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  // 열릴 때 루트로 초기화하고, 이후 폴더 이동마다 재로드.
  useEffect(() => {
    if (!open) return;
    setPath([{ id: null, name: "내 드라이브" }]);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    void load(current.id);
  }, [open, current.id, load]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const openFolder = (folder: FileNode) => setPath((p) => [...p, { id: folder.id, name: folder.name }]);
  const goToCrumb = (index: number) => setPath((p) => p.slice(0, index + 1));

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={onClose}
    >
      <div
        className="card flex max-h-[85vh] w-full max-w-lg flex-col p-0"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between gap-3 border-b border-token px-5 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="btn btn-ghost px-2 py-1" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>

        {/* breadcrumb */}
        <nav className="flex flex-wrap items-center gap-1 border-b border-token px-5 py-2 text-sm">
          {path.map((crumb, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <span className="text-muted">/</span>}
              <button
                className={`truncate rounded px-1.5 py-0.5 ${
                  i === path.length - 1
                    ? "font-semibold"
                    : "text-muted hover:text-[color:var(--text-primary)]"
                }`}
                onClick={() => goToCrumb(i)}
                disabled={i === path.length - 1}
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>

        {/* 목록 */}
        <div className="min-h-[220px] flex-1 overflow-auto px-2 py-2">
          {loading ? (
            <LoadingState label="불러오는 중..." />
          ) : error ? (
            <ErrorState message={error} onRetry={() => load(current.id)} />
          ) : items.length === 0 ? (
            <p className="px-3 py-10 text-center text-sm text-muted">이 폴더가 비어 있습니다.</p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {items.map((f) => {
                const registered = disabledIds?.has(f.id) ?? false;
                const ownedOk = pickableOwnerId == null || f.user_id === pickableOwnerId;
                return (
                  <li
                    key={f.id}
                    className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-[color:var(--bg-muted)]"
                  >
                    <button
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      onClick={() => f.is_folder && openFolder(f)}
                      disabled={!f.is_folder}
                      title={f.is_folder ? "폴더 열기" : f.name}
                    >
                      <span className={f.is_folder ? "text-[color:var(--accent)]" : "text-muted"}>
                        {f.is_folder ? (
                          <FolderIcon width={18} height={18} />
                        ) : (
                          <FileIcon width={18} height={18} />
                        )}
                      </span>
                      <span className={`truncate text-sm ${f.is_folder ? "font-medium" : ""}`}>
                        {f.name}
                      </span>
                    </button>
                    {registered ? (
                      <span className="shrink-0 text-xs text-muted">등록됨</span>
                    ) : !ownedOk ? (
                      <span className="shrink-0 text-xs text-muted">내 소유 아님</span>
                    ) : (
                      <button
                        className="btn btn-secondary shrink-0 px-2 py-1 text-xs"
                        onClick={() => onPick(f)}
                      >
                        선택
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
