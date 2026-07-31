/**
 * 이동 대상 폴더 선택기 (구글 드라이브의 "이동" 다이얼로그와 같은 형태).
 *
 * 내 드라이브 루트에서 시작해 하위 폴더를 타고 들어가며 목적지를 고른다. 목록은 폴더만
 * 받아온다(foldersOnly) — 파일이 수백 개인 폴더에서도 하위 폴더가 페이지 밖으로 밀려나지
 * 않게 하기 위함이다.
 *
 * 이동 대상이 폴더면 자기 자신 안으로는 들어갈 수 없다(백엔드도 400 으로 막지만, 들어갈 수
 * 없는 곳을 눌러보게 두지 않는다).
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { listFiles } from "@/api/files";
import type { FileNode } from "@/api/types";
import { Modal } from "@/components/Modal";
import { WikiMark } from "@/components/WikiStatusBadge";
import { ChevronRightIcon, FolderIcon } from "@/components/icons";
import { ErrorState, LoadingState } from "@/components/ui";
import { roParticle } from "@/lib/format";

/** 한 번에 받아올 하위 폴더 수. 이보다 많으면 안내만 하고 더 깊이 들어가게 둔다. */
const PAGE_SIZE = 100;

interface Crumb {
  id: number | null;
  name: string;
}

interface MoveModalProps {
  /** 이동할 항목. null 이면 닫힌 상태. */
  target: FileNode | null;
  /** 항목이 현재 들어 있는 폴더 id (내 드라이브 루트면 null). */
  currentParentId: number | null;
  onClose: () => void;
  /** 확정. 선택기가 머무는 폴더의 id 와 표시명을 함께 넘긴다(토스트 문구용). */
  onMove: (parentId: number | null, parentName: string) => Promise<void>;
}

export function MoveModal({ target, currentParentId, onClose, onMove }: MoveModalProps) {
  const [path, setPath] = useState<Crumb[]>([{ id: null, name: "내 드라이브" }]);
  const [folders, setFolders] = useState<FileNode[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const here = path[path.length - 1];

  // 항목이 바뀌면 루트에서 다시 시작한다(이전 항목의 탐색 위치가 남으면 혼란스럽다).
  useEffect(() => {
    if (target) setPath([{ id: null, name: "내 드라이브" }]);
  }, [target]);

  const load = useCallback(async (parentId: number | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listFiles(parentId, 1, PAGE_SIZE, true);
      setFolders(res.items);
      setTruncated(res.total > res.items.length);
    } catch (err) {
      setError(extractErrorMessage(err, "폴더 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (target) void load(here.id);
  }, [target, here.id, load]);

  if (!target) return null;

  // 이미 여기 있는 항목을 같은 곳으로 옮길 이유가 없다.
  const alreadyHere = here.id === currentParentId;
  // 폴더를 자기 자신 안으로는 넣을 수 없다 — 선택기 경로에 자신이 있으면 확정 불가.
  const insideSelf = target.is_folder && path.some((c) => c.id === target.id);

  const submit = async () => {
    setSubmitting(true);
    try {
      await onMove(here.id, here.name);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      title={`"${target.name}" 이동`}
      size="lg"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>
            취소
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={submitting || alreadyHere || insideSelf}
          >
            {submitting ? "이동 중…" : `"${here.name}"${roParticle(here.name)} 이동`}
          </button>
        </>
      }
    >
      {/* 선택기 내부 경로 — 각 단계를 눌러 되돌아간다. */}
      <nav className="mb-3 flex flex-wrap items-center gap-1 text-sm">
        {path.map((crumb, i) => (
          <span key={`${crumb.id}-${i}`} className="flex items-center gap-1">
            {i > 0 && <ChevronRightIcon width={14} height={14} className="text-muted" />}
            <button
              className={`truncate rounded px-1.5 py-0.5 ${
                i === path.length - 1
                  ? "font-semibold"
                  : "text-muted hover:text-[color:var(--text-primary)]"
              }`}
              onClick={() => setPath((p) => p.slice(0, i + 1))}
              disabled={i === path.length - 1}
            >
              {crumb.name}
            </button>
          </span>
        ))}
      </nav>

      <div className="min-h-[14rem] rounded-lg border border-token">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load(here.id)} />
        ) : folders.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted">하위 폴더가 없습니다.</p>
        ) : (
          <ul>
            {folders.map((f) => {
              // 이동 대상 폴더 자신은 목적지가 될 수 없으므로 눌러 들어가지 못하게 한다.
              const isSelf = f.id === target.id;
              return (
                <li key={f.id} className="border-b border-token last:border-0">
                  <button
                    className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-[color:var(--bg-muted)] disabled:opacity-40 disabled:hover:bg-transparent"
                    disabled={isSelf}
                    title={isSelf ? "자기 자신 안으로는 이동할 수 없습니다" : f.name}
                    onClick={() => setPath((p) => [...p, { id: f.id, name: f.name }])}
                  >
                    <FolderIcon width={18} height={18} className="shrink-0 text-accent" />
                    {/* 목적지 폴더의 성격만 알려준다 — 고르는 것은 사용자 몫이라 막지 않는다. */}
                    <span className="min-w-0 flex-1 truncate text-sm">
                      <WikiMark file={f} />
                      {f.name}
                    </span>
                    {!isSelf && (
                      <ChevronRightIcon width={16} height={16} className="shrink-0 text-muted" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {truncated && (
        <p className="mt-2 text-xs text-muted">
          하위 폴더가 많아 {PAGE_SIZE}개까지만 표시했습니다. 폴더를 열어 더 안쪽으로 이동하세요.
        </p>
      )}
      {alreadyHere && (
        <p className="mt-2 text-xs text-muted">이 항목은 이미 여기에 있습니다.</p>
      )}
      {insideSelf && (
        <p className="mt-2 text-xs text-danger">
          폴더를 자기 자신이나 하위 폴더로 이동할 수 없습니다.
        </p>
      )}
    </Modal>
  );
}
