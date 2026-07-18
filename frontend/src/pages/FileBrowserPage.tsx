import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import {
  abortResumableUpload,
  createFolder,
  getFile,
  listFiles,
  renameFile,
  reuploadFile,
  softDeleteFile,
  uploadFile,
} from "@/api/files";
import { checkPermission } from "@/api/permissions";
import type { FileNode } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PermissionModal } from "@/components/PermissionModal";
import { PreviewModal } from "@/components/PreviewModal";
import { ShareModal } from "@/components/ShareModal";
import { Thumbnail } from "@/components/Thumbnail";
import { VersionHistoryModal } from "@/components/VersionHistoryModal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import {
  DownloadIcon,
  EyeIcon,
  FileIcon,
  FolderIcon,
  GridIcon,
  HistoryIcon,
  ListIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RenameIcon,
  ShareIcon,
  ShieldIcon,
  TrashIcon,
  UploadIcon,
  XIcon,
} from "@/components/icons";
import { downloadFile } from "@/lib/download";
import { formatBytes, formatDateTime } from "@/lib/format";
import { permissionCovers } from "@/lib/labels";
import { fetchFilePreview } from "@/lib/preview";
import {
  forgetSession,
  listPendingSessions,
  RESUMABLE_THRESHOLD,
  resumeFromPending,
  startNewResumableUpload,
  startVersionResumableUpload,
  type PendingSession,
  type ResumableController,
  type UploadStatus,
} from "@/lib/resumable";
import { useAuthStore } from "@/store/auth";

const PAGE_SIZE = 50;
const VIEW_KEY = "minidrive:fileView";
type ViewMode = "list" | "grid";

interface Crumb {
  id: number | null;
  name: string;
}

interface UploadTask {
  id: number;
  name: string;
  percent: number;
  error?: string;
  /** 진행 상태. 재개 가능 업로드는 일시정지/취소 컨트롤을 노출한다. */
  status: UploadStatus;
  /** 재개 가능 업로드일 때만 존재 — 일시정지/재개/취소 제어에 사용. */
  controller?: ResumableController;
}

let uploadSeq = 0;

interface FileBrowserPageProps {
  /** 진입 루트 폴더 id. null 이면 내 드라이브 루트. */
  rootId?: number | null;
  /** 루트 breadcrumb 표시명. */
  rootName?: string;
  /** 공유 폴더 탐색 모드 — 내 유효 권한(check)에 따라 액션을 게이팅한다. */
  shared?: boolean;
}

export function FileBrowserPage({
  rootId = null,
  rootName = "내 드라이브",
  shared = false,
}: FileBrowserPageProps) {
  const toast = useToast();
  const refreshUser = useAuthStore((s) => s.refreshUser);

  const [path, setPath] = useState<Crumb[]>([{ id: rootId, name: rootName }]);
  const [items, setItems] = useState<FileNode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploads, setUploads] = useState<UploadTask[]>([]);
  const [view, setView] = useState<ViewMode>(
    () => (localStorage.getItem(VIEW_KEY) as ViewMode) || "list",
  );
  const [previewTarget, setPreviewTarget] = useState<FileNode | null>(null);
  // 중단된 재개 세션(새로고침 포함) — 파일 재선택으로 이어올릴 수 있다.
  const [pending, setPending] = useState<PendingSession[]>([]);
  // 이어올리기용으로 재선택을 기다리는 세션.
  const [resumeTarget, setResumeTarget] = useState<PendingSession | null>(null);
  const resumeInputRef = useRef<HTMLInputElement>(null);

  const changeView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };

  // 현재 폴더에 대한 내 유효 권한 수준 (own 모드는 항상 manage=소유자).
  const [perm, setPerm] = useState<string>(shared ? "none" : "manage");

  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [renameTarget, setRenameTarget] = useState<FileNode | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [shareTarget, setShareTarget] = useState<FileNode | null>(null);
  const [permTarget, setPermTarget] = useState<FileNode | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileNode | null>(null);
  const [versionsTarget, setVersionsTarget] = useState<FileNode | null>(null);
  // 새 버전 업로드: 파일 선택 대기 대상, 그리고 409 충돌 시 강제 덮어쓰기 후보.
  const [versionTarget, setVersionTarget] = useState<FileNode | null>(null);
  const [conflict, setConflict] = useState<{ target: FileNode; file: File } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const versionInputRef = useRef<HTMLInputElement>(null);
  const current = path[path.length - 1];
  const parentId = current.id;

  const canWrite = permissionCovers(perm, "write");
  const canManage = permissionCovers(perm, "manage");

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

  // 현재 폴더에서 시작됐다가 중단된 재개 세션을 노출한다(새로고침 후 이어올리기).
  useEffect(() => {
    const active = new Set(uploads.map((t) => t.controller?.sessionId).filter(Boolean));
    setPending(
      listPendingSessions().filter(
        (s) => s.parentId === parentId && !active.has(s.sessionId),
      ),
    );
  }, [parentId, uploads]);

  // 공유 모드: 폴더가 바뀔 때마다 현재 폴더의 내 유효 권한을 재확인해 액션을 게이팅한다.
  useEffect(() => {
    if (!shared) {
      setPerm("manage");
      return;
    }
    if (parentId == null) return;
    let cancelled = false;
    setPerm("none");
    void (async () => {
      try {
        const res = await checkPermission(parentId);
        if (!cancelled) setPerm(res.permission);
      } catch {
        if (!cancelled) setPerm("none");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [shared, parentId]);

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

  // 완료된 업로드만 잠시 후 목록에서 제거 (오류/진행 중/일시정지 항목은 유지).
  const scheduleUploadCleanup = () =>
    setTimeout(() => setUploads((u) => u.filter((t) => t.status !== "completed")), 2500);

  const patchTask = (id: number, patch: Partial<UploadTask>) =>
    setUploads((u) => u.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const afterUpload = async () => {
    await reload();
    await refreshUser();
    scheduleUploadCleanup();
  };

  // 재개 가능 업로드 컨트롤러 콜백 — 태스크 상태를 갱신하고 완료 시 목록을 새로고침한다.
  const resumableCallbacks = (id: number, name: string) => ({
    onProgress: (percent: number) => patchTask(id, { percent }),
    onStatus: (status: UploadStatus) => {
      if (status === "canceled") {
        setUploads((u) => u.filter((t) => t.id !== id));
        return;
      }
      patchTask(id, { status });
      if (status === "completed") {
        toast.success(`${name}: 업로드를 완료했습니다.`);
        void afterUpload();
      }
    },
    onError: (msg: string) => {
      patchTask(id, { error: msg, status: "error" as UploadStatus });
      toast.error(`${name}: ${msg}`);
    },
    onDone: () => {},
  });

  const runUpload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;

    for (const file of list) {
      const id = ++uploadSeq;
      setUploads((u) => [...u, { id, name: file.name, percent: 0, status: "uploading" }]);

      // 1GB 초과: 재개 가능 업로드로 분기(청킹/일시정지/재개/취소).
      if (file.size > RESUMABLE_THRESHOLD) {
        try {
          const controller = await startNewResumableUpload(
            file,
            parentId,
            resumableCallbacks(id, file.name),
          );
          patchTask(id, { controller });
          void controller.start();
        } catch (err) {
          const msg = extractErrorMessage(err, "업로드 시작 실패");
          patchTask(id, { error: msg, status: "error" });
          toast.error(`${file.name}: ${msg}`);
        }
        continue;
      }

      // 1GB 이하: 기존 단일 업로드 경로.
      try {
        await uploadFile(file, parentId, (percent) => patchTask(id, { percent }));
        patchTask(id, { percent: 100, status: "completed" });
      } catch (err) {
        const status = errorStatus(err);
        const msg =
          status === 413
            ? "저장 용량 초과"
            : status === 409
              ? "같은 이름의 파일이 있습니다"
              : status === 403 || status === 404
                ? "이 폴더에 업로드할 권한이 없습니다"
                : extractErrorMessage(err, "업로드 실패");
        patchTask(id, { error: msg, status: "error" });
        toast.error(`${file.name}: ${msg}`);
      }
      await afterUpload();
    }
  };

  // 중단된 세션을 재선택한 파일로 이어올린다(새로고침 후 복구).
  const startResume = (session: PendingSession) => {
    setResumeTarget(session);
    resumeInputRef.current?.click();
  };

  const onResumeFileSelected = async (files: FileList | null) => {
    const session = resumeTarget;
    setResumeTarget(null);
    const file = files?.[0];
    if (!session || !file) return;

    const id = ++uploadSeq;
    const name = session.kind === "version" ? `${session.name} (새 버전)` : session.name;
    setUploads((u) => [...u, { id, name, percent: 0, status: "uploading" }]);
    try {
      const controller = await resumeFromPending(session, file, resumableCallbacks(id, name));
      if (!controller) {
        setUploads((u) => u.filter((t) => t.id !== id));
        setPending((p) => p.filter((s) => s.sessionId !== session.sessionId));
        toast.error(`${session.name}: 업로드 세션이 만료되어 이어올릴 수 없습니다. 다시 업로드해 주세요.`);
        return;
      }
      patchTask(id, { controller });
      setPending((p) => p.filter((s) => s.sessionId !== session.sessionId));
      void controller.start();
    } catch (err) {
      setUploads((u) => u.filter((t) => t.id !== id));
      const msg =
        errorStatus(err) != null
          ? extractErrorMessage(err, "이어올리기 실패")
          : err instanceof Error
            ? err.message
            : "이어올리기 실패";
      toast.error(`${session.name}: ${msg}`);
    }
  };

  const cancelPending = async (session: PendingSession) => {
    forgetSession(session.sessionId);
    setPending((p) => p.filter((s) => s.sessionId !== session.sessionId));
    try {
      await abortResumableUpload(session.sessionId);
    } catch {
      /* 이미 만료/삭제됐을 수 있음 — 무시 */
    }
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
    const name = `${file.name} (새 버전)`;

    // 1GB 초과: 재개 가능 재업로드로 분기.
    if (file.size > RESUMABLE_THRESHOLD) {
      setUploads((u) => [...u, { id, name, percent: 0, status: "uploading" }]);
      try {
        const controller = await startVersionResumableUpload(
          target.id,
          file,
          baseVersion,
          parentId,
          resumableCallbacks(id, name),
        );
        patchTask(id, { controller });
        void controller.start();
      } catch (err) {
        // 개시 단계 실패는 항목을 제거하고, 409(base_version 충돌)는 충돌 모달로 유도한다.
        setUploads((u) => u.filter((t) => t.id !== id));
        const status = errorStatus(err);
        if (status === 409) {
          setConflict({ target, file });
          return;
        }
        toast.error(`${target.name}: ${extractErrorMessage(err, "새 버전 업로드 시작 실패")}`);
      }
      return;
    }

    // 1GB 이하: 기존 단일 재업로드 경로.
    setUploads((u) => [...u, { id, name, percent: 0, status: "uploading" }]);
    try {
      await reuploadFile(target.id, file, baseVersion, (percent) => patchTask(id, { percent }));
      patchTask(id, { percent: 100, status: "completed" });
      toast.success(`${target.name}: 새 버전을 업로드했습니다.`);
      await afterUpload();
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
    if (!canWrite) return;
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

  const openPreview = (file: FileNode) => {
    if (file.is_folder) return;
    setPreviewTarget(file);
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

        <div className="flex shrink-0 items-center gap-2">
          {/* 보기 전환: 목록 / 그리드(썸네일) */}
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

          {canWrite && (
            <>
              <button className="btn btn-secondary" onClick={() => setNewFolderOpen(true)}>
                <PlusIcon width={16} height={16} />
                새 폴더
              </button>
              <button className="btn btn-primary" onClick={() => fileInputRef.current?.click()}>
                <UploadIcon width={16} height={16} />
                업로드
              </button>
            </>
          )}
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
          {/* 중단된 세션 이어올리기용 재선택 입력 */}
          <input
            ref={resumeInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              void onResumeFileSelected(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {/* 본문: 드롭존 */}
      <div
        className="relative flex-1 overflow-auto p-6"
        onDragOver={(e) => {
          if (!canWrite) return;
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        {dragOver && canWrite && (
          <div
            className="pointer-events-none absolute inset-4 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-[color:var(--accent)]"
            style={{ background: "color-mix(in srgb, var(--accent) 12%, var(--bg-primary))" }}
          >
            <p className="font-medium text-[color:var(--accent)]">여기에 놓아 업로드</p>
          </div>
        )}

        {/* 중단된 재개 세션 — 파일 재선택으로 이어올리기 */}
        {pending.length > 0 && (
          <div className="mb-4 flex flex-col gap-2">
            {pending.map((s) => (
              <div
                key={s.sessionId}
                className="card flex items-center justify-between gap-3 px-4 py-2.5"
                style={{ borderColor: "var(--warning)" }}
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{s.name}</p>
                  <p className="text-xs text-muted">
                    중단된 업로드 · {formatBytes(s.size)} · 같은 파일을 다시 선택하면 이어올립니다
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button className="btn btn-secondary" onClick={() => startResume(s)}>
                    이어올리기
                  </button>
                  <button className="btn btn-ghost text-danger" onClick={() => void cancelPending(s)}>
                    취소
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 업로드 진행률 */}
        {uploads.length > 0 && (
          <div className="mb-4 flex flex-col gap-2">
            {uploads.map((t) => (
              <div key={t.id} className="card px-4 py-2.5">
                <div className="mb-1 flex items-center justify-between gap-3 text-sm">
                  <span className="truncate">{t.name}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className={t.error ? "text-danger" : "text-muted"}>
                      {t.error ??
                        (t.status === "paused"
                          ? "일시정지됨"
                          : t.status === "completed"
                            ? "완료"
                            : `${t.percent}%`)}
                    </span>
                    {/* 재개 가능 업로드만 일시정지/재개/취소 컨트롤 노출 */}
                    {t.controller && !t.error && t.status !== "completed" && (
                      <div className="flex items-center gap-1">
                        {t.status === "paused" ? (
                          <button
                            className="rounded p-1 text-muted transition-colors hover:text-[color:var(--text-primary)]"
                            title="재개"
                            aria-label="재개"
                            onClick={() => t.controller?.resume()}
                          >
                            <PlayIcon width={14} height={14} />
                          </button>
                        ) : (
                          <button
                            className="rounded p-1 text-muted transition-colors hover:text-[color:var(--text-primary)]"
                            title="일시정지"
                            aria-label="일시정지"
                            onClick={() => t.controller?.pause()}
                          >
                            <PauseIcon width={14} height={14} />
                          </button>
                        )}
                        <button
                          className="rounded p-1 text-muted transition-colors hover:text-danger"
                          title="취소"
                          aria-label="취소"
                          onClick={() => void t.controller?.cancel()}
                        >
                          <XIcon width={14} height={14} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                {!t.error && (
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted-token">
                    <div
                      className={`h-full rounded-full transition-all ${
                        t.status === "paused"
                          ? "bg-[color:var(--warning)]"
                          : "bg-[color:var(--accent)]"
                      }`}
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
            hint={
              canWrite
                ? "파일을 끌어다 놓거나 업로드 버튼을 눌러 시작하세요."
                : "표시할 항목이 없습니다."
            }
          />
        ) : (
          (() => {
            const rowProps = {
              shared,
              canWrite,
              canManage,
              onOpenFolder: openFolder,
              onPreview: openPreview,
              onDownload,
              onRename: (f: FileNode) => {
                setRenameTarget(f);
                setRenameValue(f.name);
              },
              onShare: (f: FileNode) => setShareTarget(f),
              onPermissions: (f: FileNode) => setPermTarget(f),
              onDelete: (f: FileNode) => setDeleteTarget(f),
              onVersions: (f: FileNode) => setVersionsTarget(f),
              onNewVersion: startVersionUpload,
            };
            return view === "grid" ? (
              <FileGrid items={items} {...rowProps} />
            ) : (
              <FileTable items={items} {...rowProps} />
            );
          })()
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

      <PermissionModal
        file={permTarget}
        open={permTarget !== null}
        onClose={() => setPermTarget(null)}
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

/**
 * 공유 폴더 진입 라우트 래퍼 (/shared/f/:fileId).
 * 폴더명은 route state 로 전달받고, 없으면(직접 링크/새로고침) 메타데이터로 보강한다.
 */
export function SharedFolderBrowserPage() {
  const { fileId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const id = Number(fileId);
  const stateName = (location.state as { name?: string } | null)?.name;
  const [name, setName] = useState<string | null>(stateName ?? null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (name != null || Number.isNaN(id)) return;
    let cancelled = false;
    void (async () => {
      try {
        const meta = await getFile(id);
        if (!cancelled) setName(meta.name);
      } catch {
        if (!cancelled) setNotFound(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, name]);

  if (Number.isNaN(id) || notFound) {
    return (
      <div className="flex h-screen flex-col p-6">
        <ErrorState
          message="공유 폴더를 열 수 없습니다."
          onRetry={() => navigate("/shared", { replace: true })}
        />
      </div>
    );
  }

  if (name == null) {
    return (
      <div className="flex h-screen flex-col">
        <LoadingState />
      </div>
    );
  }

  return <FileBrowserPage rootId={id} rootName={name} shared />;
}

/** 목록/그리드가 공유하는 행 동작 핸들러. */
interface FileRowProps {
  shared: boolean;
  canWrite: boolean;
  canManage: boolean;
  onOpenFolder: (f: FileNode) => void;
  onPreview: (f: FileNode) => void;
  onDownload: (f: FileNode) => void;
  onRename: (f: FileNode) => void;
  onShare: (f: FileNode) => void;
  onPermissions: (f: FileNode) => void;
  onDelete: (f: FileNode) => void;
  onVersions: (f: FileNode) => void;
  onNewVersion: (f: FileNode) => void;
}

/** 파일/폴더 한 항목의 아이콘 동작 모음 (목록·그리드 공용). */
function RowActions({ file: f, ...p }: { file: FileNode } & FileRowProps) {
  return (
    <>
      {!f.is_folder && (
        <>
          <IconAction title="미리보기" onClick={() => p.onPreview(f)}>
            <EyeIcon width={16} height={16} />
          </IconAction>
          <IconAction title="다운로드" onClick={() => p.onDownload(f)}>
            <DownloadIcon width={16} height={16} />
          </IconAction>
          {p.canWrite && (
            <IconAction title="새 버전 업로드" onClick={() => p.onNewVersion(f)}>
              <UploadIcon width={16} height={16} />
            </IconAction>
          )}
          <IconAction title="버전 기록" onClick={() => p.onVersions(f)}>
            <HistoryIcon width={16} height={16} />
          </IconAction>
          {/* 공유 링크 생성은 소유자 전용 — 공유 폴더 탐색 중에는 숨긴다. */}
          {!p.shared && (
            <IconAction title="공유" onClick={() => p.onShare(f)}>
              <ShareIcon width={16} height={16} />
            </IconAction>
          )}
        </>
      )}
      {p.canManage && (
        <IconAction title="권한 관리" onClick={() => p.onPermissions(f)}>
          <ShieldIcon width={16} height={16} />
        </IconAction>
      )}
      {p.canWrite && (
        <>
          <IconAction title="이름 변경" onClick={() => p.onRename(f)}>
            <RenameIcon width={16} height={16} />
          </IconAction>
          <IconAction title="삭제" onClick={() => p.onDelete(f)} danger>
            <TrashIcon width={16} height={16} />
          </IconAction>
        </>
      )}
    </>
  );
}

function FileTable({ items, ...p }: { items: FileNode[] } & FileRowProps) {
  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-token text-left text-xs text-muted">
            <th className="px-4 py-2.5 font-medium">이름</th>
            <th className="w-28 px-4 py-2.5 font-medium">크기</th>
            <th className="w-40 px-4 py-2.5 font-medium">수정일</th>
            <th className="w-72 px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {items.map((f) => (
            <tr key={f.id} className="group border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)]">
              <td className="px-4 py-2.5">
                <button
                  className="flex items-center gap-2.5 text-left"
                  onClick={() => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f))}
                >
                  {/* 이미지면 미니 썸네일, 아니면 유형 아이콘 */}
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
              </td>
              <td className="px-4 py-2.5 text-muted">{f.is_folder ? "-" : formatBytes(f.size)}</td>
              <td className="px-4 py-2.5 text-muted">{formatDateTime(f.updated_at)}</td>
              <td className="px-4 py-2.5">
                <div className="flex justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <RowActions file={f} {...p} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 썸네일 중심 그리드 뷰 (PRD 3.2). 폴더 클릭은 탐색, 파일 클릭은 미리보기. */
function FileGrid({ items, ...p }: { items: FileNode[] } & FileRowProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
      {items.map((f) => (
        <div key={f.id} className="group card flex flex-col overflow-hidden p-0">
          {/* 썸네일/아이콘 영역 (정사각형) */}
          <button
            className="relative flex aspect-square w-full items-center justify-center overflow-hidden bg-muted-token"
            onClick={() => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f))}
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
                  f.is_folder ? (
                    <FolderIcon width={44} height={44} />
                  ) : (
                    <FileIcon width={40} height={40} />
                  )
                }
              />
            </span>
            {!f.is_folder && f.current_version >= 2 && (
              <span className="absolute left-1.5 top-1.5">
                <Badge tone="neutral">v{f.current_version}</Badge>
              </span>
            )}
          </button>

          {/* 이름 + 동작 */}
          <div className="flex flex-col gap-1 border-t border-token px-2.5 py-2">
            <button
              className="min-w-0 text-left"
              onClick={() => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f))}
            >
              <p className={`truncate text-xs ${f.is_folder ? "font-medium" : ""}`}>{f.name}</p>
              <p className="text-[10px] text-muted">{f.is_folder ? "폴더" : formatBytes(f.size)}</p>
            </button>
            {/* 좁은 셀에서 넘치지 않도록 아이콘은 줄바꿈 허용 */}
            <div className="flex flex-wrap opacity-0 transition-opacity group-hover:opacity-100">
              <RowActions file={f} {...p} />
            </div>
          </div>
        </div>
      ))}
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
        danger ? "text-muted hover:text-danger" : "text-muted hover:text-[color:var(--text-primary)]"
      }`}
    >
      {children}
    </button>
  );
}
