import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { errorStatus, extractErrorMessage } from "@/api/client";
import {
  createFolder,
  listFiles,
  renameFile,
  reuploadFile,
  softDeleteFile,
  uploadFile,
} from "@/api/files";
import type { FileNode } from "@/api/types";
import { Modal } from "@/components/Modal";
import { ShareModal } from "@/components/ShareModal";
import { VersionHistoryModal } from "@/components/VersionHistoryModal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import {
  DownloadIcon,
  FileIcon,
  FolderIcon,
  HistoryIcon,
  PlusIcon,
  RenameIcon,
  ShareIcon,
  TrashIcon,
  UploadIcon,
} from "@/components/icons";
import { downloadFile } from "@/lib/download";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useAuthStore } from "@/store/auth";

const PAGE_SIZE = 50;

interface Crumb {
  id: number | null;
  name: string;
}

interface UploadTask {
  id: number;
  name: string;
  percent: number;
  error?: string;
}

let uploadSeq = 0;

export function FileBrowserPage() {
  const toast = useToast();
  const refreshUser = useAuthStore((s) => s.refreshUser);

  const [path, setPath] = useState<Crumb[]>([{ id: null, name: "내 드라이브" }]);
  const [items, setItems] = useState<FileNode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploads, setUploads] = useState<UploadTask[]>([]);

  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [renameTarget, setRenameTarget] = useState<FileNode | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [shareTarget, setShareTarget] = useState<FileNode | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileNode | null>(null);
  const [versionsTarget, setVersionsTarget] = useState<FileNode | null>(null);
  // 새 버전 업로드: 파일 선택 대기 대상, 그리고 409 충돌 시 강제 덮어쓰기 후보.
  const [versionTarget, setVersionTarget] = useState<FileNode | null>(null);
  const [conflict, setConflict] = useState<{ target: FileNode; file: File } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const versionInputRef = useRef<HTMLInputElement>(null);
  const current = path[path.length - 1];
  const parentId = current.id;

  const load = useCallback(
    async (pid: number | null, pageNum: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await listFiles(pid, pageNum, PAGE_SIZE);
        setItems(res.items);
        setTotal(res.total);
      } catch (err) {
        setError(extractErrorMessage(err, "목록을 불러오지 못했습니다."));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(parentId, page);
  }, [parentId, page, load]);

  const reload = () => load(parentId, page);

  // --- 탐색 -----------------------------------------------------------------

  const openFolder = (folder: FileNode) => {
    setPath((p) => [...p, { id: folder.id, name: folder.name }]);
    setPage(1);
  };

  const goToCrumb = (index: number) => {
    setPath((p) => p.slice(0, index + 1));
    setPage(1);
  };

  // --- 업로드 ---------------------------------------------------------------

  // 완료된 업로드는 잠시 후 목록에서 제거 (오류 항목은 유지).
  const scheduleUploadCleanup = () =>
    setTimeout(() => setUploads((u) => u.filter((t) => t.error)), 2500);

  const runUpload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;

    // 진행률 표시용 초기 상태 등록 (id 로 추적해 인덱스 어긋남을 방지).
    const tasks = list.map((f) => ({ id: ++uploadSeq, name: f.name, percent: 0 }));
    setUploads((u) => [...u, ...tasks]);

    for (let i = 0; i < list.length; i++) {
      const { id } = tasks[i];
      try {
        await uploadFile(list[i], parentId, (percent) => {
          setUploads((u) => u.map((t) => (t.id === id ? { ...t, percent } : t)));
        });
        setUploads((u) => u.map((t) => (t.id === id ? { ...t, percent: 100 } : t)));
      } catch (err) {
        const status = errorStatus(err);
        const msg =
          status === 413
            ? "저장 용량 초과"
            : status === 409
              ? "같은 이름의 파일이 있습니다"
              : extractErrorMessage(err, "업로드 실패");
        setUploads((u) => u.map((t) => (t.id === id ? { ...t, error: msg } : t)));
        toast.error(`${list[i].name}: ${msg}`);
      }
    }

    await reload();
    await refreshUser();
    scheduleUploadCleanup();
  };

  // --- 새 버전 업로드 -------------------------------------------------------

  const startVersionUpload = (file: FileNode) => {
    setVersionTarget(file);
    versionInputRef.current?.click();
  };

  const onVersionFileSelected = (files: FileList | null) => {
    const target = versionTarget;
    setVersionTarget(null);
    const file = files?.[0];
    if (!target || !file) return;
    // 현재 알고 있는 버전을 base_version 으로 넘겨 충돌 감지를 활성화한다.
    void runVersionUpload(target, file, target.current_version);
  };

  const runVersionUpload = async (target: FileNode, file: File, baseVersion?: number) => {
    const id = ++uploadSeq;
    setUploads((u) => [...u, { id, name: `${file.name} (새 버전)`, percent: 0 }]);
    try {
      await reuploadFile(target.id, file, baseVersion, (percent) => {
        setUploads((u) => u.map((t) => (t.id === id ? { ...t, percent } : t)));
      });
      setUploads((u) => u.map((t) => (t.id === id ? { ...t, percent: 100 } : t)));
      toast.success(`${target.name}: 새 버전을 업로드했습니다.`);
      await reload();
      await refreshUser();
      scheduleUploadCleanup();
    } catch (err) {
      // 진행 중 항목은 제거하고, 409 는 충돌 안내 모달로 유도한다.
      setUploads((u) => u.filter((t) => t.id !== id));
      const status = errorStatus(err);
      if (status === 409) {
        setConflict({ target, file });
        return;
      }
      const msg = status === 413 ? "저장 용량 초과" : extractErrorMessage(err, "새 버전 업로드 실패");
      toast.error(`${target.name}: ${msg}`);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) void runUpload(e.dataTransfer.files);
  };

  // --- 폴더/이름/삭제 -------------------------------------------------------

  const submitNewFolder = async () => {
    const name = folderName.trim();
    if (!name) return;
    try {
      await createFolder(name, parentId);
      toast.success("폴더를 만들었습니다.");
      setNewFolderOpen(false);
      setFolderName("");
      await reload();
    } catch (err) {
      toast.error(extractErrorMessage(err, "폴더 생성에 실패했습니다."));
    }
  };

  const submitRename = async () => {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name || name === renameTarget.name) {
      setRenameTarget(null);
      return;
    }
    try {
      await renameFile(renameTarget.id, name);
      toast.success("이름을 변경했습니다.");
      setRenameTarget(null);
      await reload();
    } catch (err) {
      toast.error(extractErrorMessage(err, "이름 변경에 실패했습니다."));
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await softDeleteFile(deleteTarget.id);
      toast.success("휴지통으로 이동했습니다.");
      setDeleteTarget(null);
      await reload();
      await refreshUser();
    } catch (err) {
      toast.error(extractErrorMessage(err, "삭제에 실패했습니다."));
      setDeleteTarget(null);
    }
  };

  const onDownload = async (file: FileNode) => {
    try {
      await downloadFile(file.id);
    } catch (err) {
      toast.error(extractErrorMessage(err, "다운로드에 실패했습니다."));
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen flex-col">
      {/* 헤더: breadcrumb + 액션 */}
      <div className="flex items-center justify-between gap-4 border-b border-token px-6 py-4">
        <nav className="flex min-w-0 items-center gap-1 text-sm">
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

        <div className="flex shrink-0 gap-2">
          <button className="btn btn-secondary" onClick={() => setNewFolderOpen(true)}>
            <PlusIcon width={16} height={16} />
            새 폴더
          </button>
          <button className="btn btn-primary" onClick={() => fileInputRef.current?.click()}>
            <UploadIcon width={16} height={16} />
            업로드
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files) void runUpload(e.target.files);
              e.target.value = "";
            }}
          />
          <input
            ref={versionInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              onVersionFileSelected(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {/* 본문: 드롭존 */}
      <div
        className="relative flex-1 overflow-auto p-6"
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        {dragOver && (
          <div className="pointer-events-none absolute inset-4 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-[color:var(--accent)] bg-blue-50/70">
            <p className="font-medium text-[color:var(--accent)]">여기에 놓아 업로드</p>
          </div>
        )}

        {/* 업로드 진행률 */}
        {uploads.length > 0 && (
          <div className="mb-4 flex flex-col gap-2">
            {uploads.map((t, i) => (
              <div key={i} className="card px-4 py-2.5">
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="truncate">{t.name}</span>
                  <span className={t.error ? "text-red-600" : "text-muted"}>
                    {t.error ?? `${t.percent}%`}
                  </span>
                </div>
                {!t.error && (
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted-token">
                    <div
                      className="h-full rounded-full bg-[color:var(--accent)] transition-all"
                      style={{ width: `${t.percent}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={reload} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<FolderIcon width={40} height={40} />}
            title="이 폴더가 비어 있습니다"
            hint="파일을 끌어다 놓거나 업로드 버튼을 눌러 시작하세요."
          />
        ) : (
          <FileTable
            items={items}
            onOpenFolder={openFolder}
            onDownload={onDownload}
            onRename={(f) => {
              setRenameTarget(f);
              setRenameValue(f.name);
            }}
            onShare={(f) => setShareTarget(f)}
            onDelete={(f) => setDeleteTarget(f)}
            onVersions={(f) => setVersionsTarget(f)}
            onNewVersion={startVersionUpload}
          />
        )}

        {/* 페이지네이션 */}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-center gap-3 text-sm">
            <button
              className="btn btn-secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              이전
            </button>
            <span className="text-muted">
              {page} / {totalPages}
            </span>
            <button
              className="btn btn-secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </button>
          </div>
        )}
      </div>

      {/* 새 폴더 모달 */}
      <Modal
        open={newFolderOpen}
        title="새 폴더"
        onClose={() => setNewFolderOpen(false)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setNewFolderOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitNewFolder}>
              만들기
            </button>
          </>
        }
      >
        <input
          className="input"
          placeholder="폴더 이름"
          value={folderName}
          onChange={(e) => setFolderName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitNewFolder()}
          autoFocus
        />
      </Modal>

      {/* 이름 변경 모달 */}
      <Modal
        open={renameTarget !== null}
        title="이름 변경"
        onClose={() => setRenameTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setRenameTarget(null)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitRename}>
              변경
            </button>
          </>
        }
      >
        <input
          className="input"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitRename()}
          autoFocus
        />
      </Modal>

      {/* 삭제 확인 */}
      <Modal
        open={deleteTarget !== null}
        title="휴지통으로 이동"
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmDelete}>
              휴지통으로 이동
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{deleteTarget?.name}</span>
          {deleteTarget?.is_folder ? " 폴더와 하위 항목을" : " 항목을"} 휴지통으로
          이동하시겠습니까?
        </p>
      </Modal>

      {/* 버전 충돌 안내 */}
      <Modal
        open={conflict !== null}
        title="버전 충돌"
        onClose={() => setConflict(null)}
        footer={
          <>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setConflict(null);
                void reload();
              }}
            >
              새로고침 후 다시 시도
            </button>
            <button
              className="btn btn-primary"
              onClick={() => {
                const c = conflict;
                setConflict(null);
                if (c) void runVersionUpload(c.target, c.file);
              }}
            >
              강제 덮어쓰기
            </button>
          </>
        }
      >
        <p className="text-sm">
          다른 사용자가 먼저 새 버전을 올렸습니다. 목록을 새로고침해 최신 버전을 확인한 뒤 다시
          시도하거나, 그대로 덮어쓸 수 있습니다. (강제 덮어쓰기해도 기존 이력은 보존됩니다.)
        </p>
      </Modal>

      <ShareModal
        file={shareTarget}
        open={shareTarget !== null}
        onClose={() => setShareTarget(null)}
        onCreated={() => toast.success("공유 링크를 만들었습니다.")}
      />

      <VersionHistoryModal
        file={versionsTarget}
        open={versionsTarget !== null}
        onClose={() => setVersionsTarget(null)}
        onChanged={() => {
          void reload();
          void refreshUser();
        }}
      />
    </div>
  );
}

function FileTable({
  items,
  onOpenFolder,
  onDownload,
  onRename,
  onShare,
  onDelete,
  onVersions,
  onNewVersion,
}: {
  items: FileNode[];
  onOpenFolder: (f: FileNode) => void;
  onDownload: (f: FileNode) => void;
  onRename: (f: FileNode) => void;
  onShare: (f: FileNode) => void;
  onDelete: (f: FileNode) => void;
  onVersions: (f: FileNode) => void;
  onNewVersion: (f: FileNode) => void;
}) {
  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-token text-left text-xs text-muted">
            <th className="px-4 py-2.5 font-medium">이름</th>
            <th className="w-28 px-4 py-2.5 font-medium">크기</th>
            <th className="w-40 px-4 py-2.5 font-medium">수정일</th>
            <th className="w-56 px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {items.map((f) => (
            <tr key={f.id} className="group border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)]">
              <td className="px-4 py-2.5">
                <button
                  className="flex items-center gap-2.5 text-left"
                  onClick={() => f.is_folder && onOpenFolder(f)}
                  disabled={!f.is_folder}
                >
                  <span className={f.is_folder ? "text-[color:var(--accent)]" : "text-muted"}>
                    {f.is_folder ? <FolderIcon /> : <FileIcon />}
                  </span>
                  <span className={`truncate ${f.is_folder ? "font-medium" : ""}`}>{f.name}</span>
                  {!f.is_folder && f.current_version >= 2 && (
                    <Badge tone="neutral">v{f.current_version}</Badge>
                  )}
                </button>
              </td>
              <td className="px-4 py-2.5 text-muted">{f.is_folder ? "-" : formatBytes(f.size)}</td>
              <td className="px-4 py-2.5 text-muted">{formatDateTime(f.updated_at)}</td>
              <td className="px-4 py-2.5">
                <div className="flex justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                  {!f.is_folder && (
                    <>
                      <IconAction title="다운로드" onClick={() => onDownload(f)}>
                        <DownloadIcon width={16} height={16} />
                      </IconAction>
                      <IconAction title="새 버전 업로드" onClick={() => onNewVersion(f)}>
                        <UploadIcon width={16} height={16} />
                      </IconAction>
                      <IconAction title="버전 기록" onClick={() => onVersions(f)}>
                        <HistoryIcon width={16} height={16} />
                      </IconAction>
                      <IconAction title="공유" onClick={() => onShare(f)}>
                        <ShareIcon width={16} height={16} />
                      </IconAction>
                    </>
                  )}
                  <IconAction title="이름 변경" onClick={() => onRename(f)}>
                    <RenameIcon width={16} height={16} />
                  </IconAction>
                  <IconAction title="삭제" onClick={() => onDelete(f)} danger>
                    <TrashIcon width={16} height={16} />
                  </IconAction>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IconAction({
  title,
  onClick,
  danger,
  children,
}: {
  title: string;
  onClick: () => void;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      aria-label={title}
      onClick={onClick}
      className={`rounded-md p-1.5 transition-colors hover:bg-[color:var(--bg-secondary)] ${
        danger ? "text-muted hover:text-red-600" : "text-muted hover:text-[color:var(--text-primary)]"
      }`}
    >
      {children}
    </button>
  );
}
