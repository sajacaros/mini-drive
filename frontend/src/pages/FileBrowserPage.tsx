import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import {
  abortResumableUpload,
  addFavorite,
  createFolder,
  getFile,
  listFiles,
  listRecent,
  moveFile,
  removeFavorite,
  renameFile,
  reuploadFile,
  softDeleteFile,
  uploadFile,
} from "@/api/files";
import { checkPermission, listSharedWithMe } from "@/api/permissions";
import type { FileNode } from "@/api/types";
import { FavoriteStar } from "@/components/FavoriteStar";
import { Modal } from "@/components/Modal";
import { MoveModal } from "@/components/MoveModal";
import { PageHeader } from "@/components/PageHeader";
import { PermissionModal } from "@/components/PermissionModal";
import { WikiModal } from "@/components/WikiModal";
import { WikiMark } from "@/components/WikiStatusBadge";
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
  FolderUploadIcon,
  GridIcon,
  HistoryIcon,
  InboxIcon,
  ListIcon,
  MoveIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RenameIcon,
  ShareIcon,
  ShieldIcon,
  BookIcon,
  TrashIcon,
  UploadIcon,
  XIcon,
} from "@/components/icons";
import { downloadArchive, downloadNode } from "@/lib/download";
import { InlineNameInput } from "@/components/InlineName";
import { subscribeFileEvents } from "@/lib/fileEvents";
import { formatBytes, formatDateTime, roParticle } from "@/lib/format";
import { permissionCovers, permissionLabel, permissionTone } from "@/lib/labels";
import { fetchFilePreview } from "@/lib/preview";
import {
  CARD_ACTIVE_CLASS,
  CARD_SELECTED_CLASS,
  ROW_ACTION_PROPS,
  ROW_ACTIVE_CLASS,
  ROW_BASE_CLASS,
  ROW_SELECTED_CLASS,
  rowOpenHandlers,
  useCoarsePointer,
  type SelectMods,
} from "@/lib/rowOpen";
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
import {
  collectFromEntries,
  collectFromInput,
  dropHasDirectory,
  entriesFromDrop,
  type CollectedTree,
} from "@/lib/fileTree";
import {
  preflight,
  runFolderUpload,
  type PreflightResult,
  type SkippedEntry,
} from "@/lib/folderUpload";
import { uploadErrorFromException } from "@/lib/uploadError";
import { useAuthStore } from "@/store/auth";

const PAGE_SIZE = 50;
const VIEW_KEY = "minidrive:fileView";
type ViewMode = "list" | "grid";

/**
 * "공유" 가상 폴더의 crumb id. 실제 파일 id 는 항상 양수이므로 음수 센티넬로 구분한다.
 * 이 crumb 이 경로에 포함되면 "공유받은 항목" 하위 트리(읽기 전용 컨테이너 + 권한 게이팅)로 취급한다.
 */
const SHARED_VIRTUAL_ID = -1;

/** 권한 수준 순위 — 다중 그룹 공유 시 최고 권한을 고를 때 사용. */
const PERM_RANK: Record<string, number> = { read: 1, write: 2, manage: 3, owner: 4 };

/**
 * 페이지 안에서 시작된 항목 드래그를 표시하는 커스텀 MIME. OS 파일 드롭(업로드)과 구분하는
 * 유일한 단서다 — dragover 시점에는 dataTransfer 의 **값**을 읽을 수 없고 types 만 볼 수 있어,
 * 값이 아니라 타입의 존재 여부로 판단한다.
 */
const DRAG_MIME = "application/x-minidrive-item";

function isInternalDrag(e: DragEvent): boolean {
  return Array.from(e.dataTransfer.types).includes(DRAG_MIME);
}

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
  /**
   * 폴더 업로드에서 실패한 항목들. 완료 직후 뜨는 모달을 닫아도 다시 볼 수 있어야 하므로
   * 태스크에 보관한다(모달 상태에만 두면 닫는 순간 사유를 영영 확인할 수 없다).
   */
  failures?: SkippedEntry[];
}

let uploadSeq = 0;

interface FileBrowserPageProps {
  /** 진입 루트 폴더 id. null 이면 내 드라이브 루트. */
  rootId?: number | null;
  /** 루트 breadcrumb 표시명. */
  rootName?: string;
  /**
   * 초기 breadcrumb 경로. 지정하면 rootId/rootName 대신 사용한다.
   * 공유 폴더 딥링크(/shared/f/:id)를 "내 드라이브 > 공유 > 폴더" 아래에 seed 할 때 쓴다.
   */
  initialPath?: Crumb[];
}

export function FileBrowserPage({
  rootId = null,
  rootName = "내 드라이브",
  initialPath,
}: FileBrowserPageProps) {
  const toast = useToast();
  const refreshUser = useAuthStore((s) => s.refreshUser);
  const me = useAuthStore((s) => s.user);

  const [path, setPath] = useState<Crumb[]>(
    () => initialPath ?? [{ id: rootId, name: rootName }],
  );
  const [items, setItems] = useState<FileNode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  /*
    지금 폴더가 전사 위키인가. 여기 올리는 md·html 은 올리는 즉시 전 구성원에게 공개되므로,
    올리기 전에 알려야 한다 — 특히 `write` 권한만 가진 사람이 남의 폴더에 올릴 때가 그렇다
    (spec/wiki-index.md 「폴더 상속 사고 경로」).
  */
  const [wikiFolder, setWikiFolder] = useState(false);
  const [uploads, setUploads] = useState<UploadTask[]>([]);
  const [view, setView] = useState<ViewMode>(
    () => (localStorage.getItem(VIEW_KEY) as ViewMode) || "list",
  );
  const [previewTarget, setPreviewTarget] = useState<FileNode | null>(null);
  /**
   * 체크 선택된 항목들 — 선택 액션 바(일괄 다운로드)의 대상이다. 체크박스, Ctrl/Cmd 토글,
   * Shift 범위로만 담긴다. 클릭 한 번은 여기에 손대지 않는다(→ activeId).
   * 목록이 바뀌면 아래 effect 에서 해제한다.
   */
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  /**
   * 클릭 한 번으로 잡히는 "현재 항목". 하이라이트와 F2 이름 편집의 기준일 뿐이라 체크와는
   * 따로 논다 — 폴더를 더블클릭해 들어갈 때 체크가 딸려 들어오지 않게 하려는 분리다.
   * "공유" 가상 행은 SHARED_VIRTUAL_ID 로 여기에만 담긴다(체크 대상이 아니다).
   */
  const [activeId, setActiveId] = useState<number | null>(null);
  /** Shift 범위 선택의 기준점 — 마지막으로 클릭/토글한 항목. */
  const anchorRef = useRef<number | null>(null);
  // 드라이브 홈(루트)에서만 노출하는 "최근 항목" 스트립 데이터 (Phase 8-3).
  const [recent, setRecent] = useState<FileNode[]>([]);
  // 중단된 재개 세션(새로고침 포함) — 파일 재선택으로 이어올릴 수 있다.
  const [pending, setPending] = useState<PendingSession[]>([]);
  // 이어올리기용으로 재선택을 기다리는 세션.
  const [resumeTarget, setResumeTarget] = useState<PendingSession | null>(null);
  const resumeInputRef = useRef<HTMLInputElement>(null);

  const changeView = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  };

  // 현재 폴더에 대한 내 유효 권한 수준 (내 소유 폴더는 항상 manage=소유자).
  // 공유 하위 트리로 seed 된 경우(딥링크)는 "none"에서 시작해 checkPermission 으로 보정한다.
  const [perm, setPerm] = useState<string>(
    (initialPath ?? []).some((c) => c.id === SHARED_VIRTUAL_ID) ? "none" : "manage",
  );

  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  // 제자리 편집 중인 항목 id (F2 / 이름 재클릭 / 행 액션으로 진입).
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [moveTarget, setMoveTarget] = useState<FileNode | null>(null);
  /**
   * 드롭 하이라이트 대상. 폴더 행은 id, breadcrumb 은 그 crumb 의 id(루트는 null)라
   * 둘을 구분하려고 종류를 함께 담는다.
   */
  const [dropTarget, setDropTarget] = useState<{ kind: "row" | "crumb"; id: number | null } | null>(
    null,
  );
  const [shareTarget, setShareTarget] = useState<FileNode | null>(null);
  const [wikiTarget, setWikiTarget] = useState<FileNode | null>(null);
  const [permTarget, setPermTarget] = useState<FileNode | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileNode | null>(null);
  const [versionsTarget, setVersionsTarget] = useState<FileNode | null>(null);
  // 새 버전 업로드: 파일 선택 대기 대상, 그리고 409 충돌 시 강제 덮어쓰기 후보.
  const [versionTarget, setVersionTarget] = useState<FileNode | null>(null);
  const [conflict, setConflict] = useState<{ target: FileNode; file: File } | null>(null);

  // 폴더 업로드: 사전 검사 결과(확인 다이얼로그) 와 완료 후 실패 목록.
  const [folderPlan, setFolderPlan] = useState<{ tree: CollectedTree; plan: PreflightResult } | null>(
    null,
  );
  const [folderFailures, setFolderFailures] = useState<SkippedEntry[] | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const versionInputRef = useRef<HTMLInputElement>(null);
  /**
   * 현재 끌고 있는 항목. dataTransfer 에는 id 만 실어 보내고(브라우저가 drag 중에는 값을
   * 읽지 못하게 막는다) 실제 노드는 ref 로 들고 있는다 — 같은 페이지 안의 이동이라 충분하다.
   */
  const draggingRef = useRef<FileNode | null>(null);
  const current = path[path.length - 1];
  const parentId = current.id;

  // 현재 경로가 "공유" 가상 폴더 목록 자체인지 (읽기 전용 컨테이너).
  const atVirtualShared = current.id === SHARED_VIRTUAL_ID;
  // 현재 경로가 "공유" 하위 트리(가상 목록 또는 그 안의 실제 공유 폴더)인지.
  const inSharedSubtree = path.some((c) => c.id === SHARED_VIRTUAL_ID);
  // 내 드라이브 루트 목록(첫 페이지)에서만 상단에 "공유" 가상 폴더 행을 고정 노출한다.
  const showSharedVirtualRow = !inSharedSubtree && parentId == null && page === 1;

  const canWrite = permissionCovers(perm, "write");
  const canManage = permissionCovers(perm, "manage");

  const load = useCallback(
    async (pid: number | null, pageNum: number) => {
      setLoading(true);
      setError(null);
      try {
        if (pid === SHARED_VIRTUAL_ID) {
          // "공유" 가상 폴더: 내 소속 그룹으로 공유받은 항목을 한 화면에 모은다.
          // 같은 파일이 여러 그룹으로 공유되면 부여행 전체에 걸쳐 그룹명을 합집합으로 병합하고,
          // 권한은 최고 수준(read<write<manage)을 유지해 한 행으로 정리한다.
          const res = await listSharedWithMe();
          const byId = new Map<number, FileNode>();
          for (const it of res.items) {
            // 부여행마다 그룹을 모은다: grant 레벨 group_name(부여 그룹, 항상 단일)과
            // 임베드 file.group_names 를 합친다. 임베드 값이 부여 그룹을 반영하지 않을 수 있어
            // group_name 을 fallback 이 아니라 항상 포함한다.
            const rowGroups = [it.group_name, ...(it.file.group_names ?? [])].filter(Boolean);
            const rowPerm = (it.file.permission ??
              it.permission) as FileNode["permission"];
            const existing = byId.get(it.file.id);
            if (existing) {
              existing.group_names = Array.from(
                new Set([...(existing.group_names ?? []), ...rowGroups]),
              ).sort();
              if ((PERM_RANK[rowPerm ?? ""] ?? 0) > (PERM_RANK[existing.permission ?? ""] ?? 0)) {
                existing.permission = rowPerm;
              }
            } else {
              byId.set(it.file.id, {
                ...it.file,
                group_names: Array.from(new Set(rowGroups)).sort(),
                permission: rowPerm,
              });
            }
          }
          const rows = Array.from(byId.values());
          setItems(rows);
          setTotal(rows.length);
          setWikiFolder(false); // 가상 "공유" 폴더에는 올릴 수 없어 경고할 대상이 없다.
          return;
        }
        const res = await listFiles(pid, pageNum, PAGE_SIZE);
        setItems(res.items);
        setTotal(res.total);
        setWikiFolder(res.wiki_enabled ?? false);
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

  // --- 실시간 반영 (SSE, Phase 8-1) -----------------------------------------
  // 구독은 마운트~언마운트 1회만 유지하고, 현재 폴더/페이지는 ref 로 읽어 재구독을 피한다.
  const parentIdRef = useRef(parentId);
  const pageRef = useRef(page);
  parentIdRef.current = parentId;
  pageRef.current = page;
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 연속 이벤트를 300ms 로 병합해 현재 폴더를 재조회한다.
  const scheduleReload = useCallback(() => {
    if (reloadTimer.current) clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(() => {
      void load(parentIdRef.current, pageRef.current);
    }, 300);
  }, [load]);

  useEffect(() => {
    const unsub = subscribeFileEvents({
      onEvent: (e) => {
        // 영구 삭제는 휴지통 화면에서만 의미가 있다. 삭제 루트의 부모는 (그게 삭제 루트인
        // 조건이라) 살아 있는 폴더이므로, 걸러내지 않으면 그 폴더를 보고 있는 사용자에게
        // 무의미한 재조회를 일으킨다 — 영구 삭제된 항목은 어차피 이 목록에 없다.
        if (e.type === "purge") return;
        // 위키 인덱싱 완료 — 배지가 pending/indexing 에서 ready 로 바뀌어야 한다.
        // 발행 이벤트에 parent_folder_id 가 실려 있어 아래 폴더 일치 조건을 그대로 탄다.
        // 현재 보고 있는 폴더의 변경만 반영(다른 폴더 이벤트는 무시). 내 액션 에코도 함께
        // 재조회한다(설계상 v1 단순화 허용).
        if (e.parent_folder_id === parentIdRef.current) scheduleReload();
      },
      // 재연결 성공 시 끊긴 동안의 변경을 보정하기 위해 1회 재조회.
      onReconnect: scheduleReload,
    });
    return () => {
      unsub();
      if (reloadTimer.current) clearTimeout(reloadTimer.current);
    };
  }, [scheduleReload]);

  // --- 최근 항목 스트립 (Phase 8-3) — 내 드라이브 루트에서만 노출 -------------
  const showRecentStrip = !inSharedSubtree && parentId == null;
  useEffect(() => {
    if (!showRecentStrip) {
      setRecent([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rows = await listRecent(6);
        if (!cancelled) setRecent(rows);
      } catch {
        if (!cancelled) setRecent([]);
      }
    })();
    return () => {
      cancelled = true;
    };
    // 루트로 돌아오거나 목록이 갱신될 때 최근도 함께 새로고친다.
  }, [showRecentStrip, items]);

  // --- 즐겨찾기 토글 (Phase 8-2) — 낙관적 갱신, 실패 시 롤백 ------------------
  const onToggleFavorite = useCallback(
    async (f: FileNode) => {
      const next = !f.is_favorite;
      setItems((list) =>
        list.map((it) => (it.id === f.id ? { ...it, is_favorite: next } : it)),
      );
      try {
        await (next ? addFavorite(f.id) : removeFavorite(f.id));
      } catch (err) {
        setItems((list) =>
          list.map((it) => (it.id === f.id ? { ...it, is_favorite: f.is_favorite } : it)),
        );
        toast.error(extractErrorMessage(err, "즐겨찾기 변경에 실패했습니다."));
      }
    },
    [toast],
  );

  // 현재 폴더에서 시작됐다가 중단된 재개 세션을 노출한다(새로고침 후 이어올리기).
  useEffect(() => {
    const active = new Set(uploads.map((t) => t.controller?.sessionId).filter(Boolean));
    setPending(
      listPendingSessions().filter(
        (s) => s.parentId === parentId && !active.has(s.sessionId),
      ),
    );
  }, [parentId, uploads]);

  // 폴더가 바뀔 때마다 현재 폴더의 내 유효 권한을 재확인해 액션을 게이팅한다.
  // - "공유" 가상 목록: 읽기 전용 컨테이너 → 쓰기/관리 차단(none).
  // - 공유 하위 트리의 실제 폴더: checkPermission 으로 유효 권한 판정.
  // - 내 소유 폴더: 항상 manage.
  useEffect(() => {
    if (atVirtualShared) {
      setPerm("none");
      return;
    }
    if (!inSharedSubtree) {
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
  }, [atVirtualShared, inSharedSubtree, parentId]);

  const reload = () => load(parentId, page);

  // 폴더를 옮기거나 페이지를 넘기면 이전 선택/편집은 화면에 없으므로 해제한다.
  useEffect(() => {
    setSelectedIds([]);
    setActiveId(null);
    anchorRef.current = null;
    setRenamingId(null);
  }, [parentId, page]);

  // --- 현재 항목(클릭) / 체크 선택(체크박스·Ctrl·Shift) ------------------------

  const allSelected = items.length > 0 && items.every((f) => selectedIds.includes(f.id));

  /** 클릭 한 번 — 현재 항목만 옮긴다. 체크 선택은 건드리지 않는다. */
  const focusItem = (id: number) => {
    setActiveId(id);
    anchorRef.current = id;
  };

  /** 체크 선택 변경 — 체크박스(누적 토글)와 Ctrl/Shift 클릭이 함께 쓴다. */
  const selectItem = (id: number, mods: SelectMods) => {
    setActiveId(id);
    setSelectedIds((prev) => {
      // Shift 범위는 화면 순서(items)를 좌표계로 쓴다. 기준점이나 대상이 목록 밖이면
      // (예: "공유" 가상 행) 범위를 만들 수 없으므로 토글로 떨어진다.
      if (mods.range && anchorRef.current !== null) {
        const ids = items.map((f) => f.id);
        const from = ids.indexOf(anchorRef.current);
        const to = ids.indexOf(id);
        if (from >= 0 && to >= 0) {
          const [start, end] = from <= to ? [from, to] : [to, from];
          return ids.slice(start, end + 1);
        }
      }
      anchorRef.current = id;
      return prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
    });
  };

  const clearSelection = () => {
    anchorRef.current = null;
    setSelectedIds([]);
  };

  const toggleSelectAll = () => {
    anchorRef.current = null;
    setSelectedIds(allSelected ? [] : items.map((f) => f.id));
  };

  // 현재 항목을 F2 로 제자리 편집 (탐색기 관례). 대상은 체크 선택이 아니라 클릭으로 잡힌
  // 항목 하나다. 입력/다이얼로그 안에서 누른 키는 그쪽에서 stopPropagation 하므로 여기까지
  // 오지 않는다.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "F2" || activeId === null) return;
      const target = items.find((f) => f.id === activeId);
      if (!target || !rowCanWrite(target, canWrite)) return;
      e.preventDefault();
      setRenamingId(target.id);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeId, items, canWrite]);

  // --- 탐색 -----------------------------------------------------------------

  const openFolder = (folder: FileNode) => {
    // 같은 폴더를 연속 클릭(더블클릭)해도 crumb 이 중복 쌓이지 않도록 가드.
    setPath((p) => (p[p.length - 1]?.id === folder.id ? p : [...p, { id: folder.id, name: folder.name }]));
    setPage(1);
  };

  // "공유" 가상 폴더 진입 — 내 드라이브 루트에서만 노출되는 고정 행.
  const openSharedVirtual = () => {
    setPath((p) =>
      p[p.length - 1]?.id === SHARED_VIRTUAL_ID ? p : [...p, { id: SHARED_VIRTUAL_ID, name: "공유" }],
    );
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

  // 업로드 완료 알림 — 전송 경로(단일/재개)와 무관하게 동일한 완료 피드백을 준다.
  const notifyUploadSuccess = (name: string) =>
    toast.success(`${name}: 업로드를 완료했습니다.`);

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
        notifyUploadSuccess(name);
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
        notifyUploadSuccess(file.name);
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

  // --- 폴더 업로드 ----------------------------------------------------------

  /**
   * 남은 저장 용량. 사전 검사에서 총량과 비교한다(강제는 서버가 한다).
   * 사용자 정보가 아직 없으면 무한대로 둔다 — 모르는 상태를 "용량 0"으로 오해해
   * 정상 업로드를 막는 것보다, 서버 판정에 맡기는 편이 낫다.
   */
  const remainingBytes = me ? Math.max(0, me.max_storage - me.storage_used) : Infinity;

  /** 트리 수집 → 사전 검사 → 확인 다이얼로그. 바이트 하나 보내기 전에 전부 검증한다. */
  const planFolderUpload = (tree: CollectedTree) => {
    if (tree.files.length === 0) {
      toast.error("업로드할 파일이 없습니다.");
      return;
    }
    const plan = preflight(tree, remainingBytes);
    if (plan.entries.length === 0) {
      toast.error("업로드할 수 있는 파일이 없습니다.");
      setFolderPlan({ tree, plan }); // 사유는 다이얼로그에서 보여준다.
      return;
    }
    setFolderPlan({ tree, plan });
  };

  const startFolderUpload = async () => {
    const current = folderPlan;
    setFolderPlan(null);
    if (!current) return;
    const { plan } = current;

    const id = ++uploadSeq;
    const label = plan.entries[0]?.path.split("/")[0] ?? "폴더";
    const total = plan.entries.length;
    setUploads((u) => [
      ...u,
      { id, name: `${label} (0/${total})`, percent: 0, status: "uploading" },
    ]);

    let result;
    try {
      result = await runFolderUpload(plan, parentId, {
        onProgress: (bytes) =>
          patchTask(id, {
            percent: plan.totalBytes > 0 ? Math.min(100, Math.round((bytes / plan.totalBytes) * 100)) : 100,
          }),
        onCount: (done) => patchTask(id, { name: `${label} (${done}/${total})` }),
      });
    } catch (err) {
      const msg = uploadErrorFromException(err);
      patchTask(id, { error: msg, status: "error" });
      toast.error(`${label}: ${msg}`);
      return;
    }

    // 사전 검사에서 건너뛴 항목도 실패로 함께 보고한다.
    const failures = [...plan.skipped, ...result.failed];
    patchTask(id, {
      percent: 100,
      status: failures.length > 0 ? "error" : "completed",
      name: `${label} (${result.succeeded}/${total})`,
      error: failures.length > 0 ? `${failures.length}개 실패` : undefined,
      failures: failures.length > 0 ? failures : undefined,
    });

    if (failures.length === 0) {
      toast.success(`${result.succeeded}개 파일을 업로드했습니다.`);
    } else if (result.succeeded > 0) {
      toast.error(`${result.succeeded}개 완료 · ${failures.length}개 실패`);
    } else {
      toast.error("업로드에 실패했습니다.");
    }
    if (failures.length > 0) setFolderFailures(failures);

    // 파일마다 갱신하지 않고 전체가 끝난 뒤 한 번만 새로고침한다(SSE 이벤트 폭주 회피).
    await afterUpload();
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    // 페이지 안에서 항목을 끌어온 것이면 업로드가 아니라 "현재 폴더로 이동"이다.
    if (isInternalDrag(e)) {
      onDropOnFolder(e, parentId, current.name);
      return;
    }
    if (!canWrite) return;
    // dataTransfer 는 핸들러가 반환되면 무효화되므로 await 전에 동기적으로 꺼낸다.
    const entries = entriesFromDrop(e.dataTransfer);
    if (dropHasDirectory(entries)) {
      void collectFromEntries(entries).then(planFolderUpload);
      return;
    }
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

  // --- 이동 (드래그 앤 드롭 + 이동 다이얼로그) ------------------------------

  /**
   * 항목을 다른 폴더로 옮긴다. 성공/실패 토스트까지 여기서 처리해 드롭과 다이얼로그가
   * 같은 동작을 공유하게 한다. 대상이 현재 폴더와 같으면 요청조차 보내지 않는다.
   */
  const runMove = async (file: FileNode, targetId: number | null, targetName: string) => {
    if (targetId === parentId) return;
    try {
      await moveFile(file.id, targetId);
      toast.success(
        `"${file.name}"을(를) "${targetName}"${roParticle(targetName)} 이동했습니다.`,
      );
      await reload();
    } catch (err) {
      toast.error(extractErrorMessage(err, "이동에 실패했습니다."));
    }
  };

  const submitMove = async (targetId: number | null, targetName: string) => {
    if (!moveTarget) return;
    await runMove(moveTarget, targetId, targetName);
    setMoveTarget(null);
  };

  /** 드롭 대상(폴더 행 / breadcrumb)이 항목을 받았을 때. */
  const onDropOnFolder = (e: DragEvent, targetId: number | null, targetName: string) => {
    const dragged = draggingRef.current;
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);
    setDragOver(false);
    draggingRef.current = null;
    if (!dragged || dragged.id === targetId) return;
    void runMove(dragged, targetId, targetName);
  };

  /** 제자리 편집 확정. 실패해도 편집은 닫고(목록엔 원래 이름이 남는다) 사유만 알린다. */
  const submitRename = async (file: FileNode, raw: string) => {
    setRenamingId(null);
    const name = raw.trim();
    if (!name || name === file.name) return;
    try {
      await renameFile(file.id, name);
      toast.success("이름을 변경했습니다.");
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

  /** 한 항목 다운로드 — 파일은 원본 그대로, 폴더는 하위 전체를 ZIP 으로 묶어 받는다. */
  const onDownload = async (file: FileNode) => {
    try {
      await downloadNode(file);
    } catch (err) {
      toast.error(extractErrorMessage(err, "다운로드에 실패했습니다."));
    }
  };

  /**
   * 선택한 항목 다운로드. 파일 하나뿐이면 굳이 ZIP 으로 감싸지 않고 원본을 그대로 준다.
   * 개수·용량 상한 초과(413) 등 서버 안내는 그대로 토스트에 싣는다.
   */
  const downloadSelection = async () => {
    const ids = selectedIds;
    if (ids.length === 0) return;
    const single = ids.length === 1 ? items.find((f) => f.id === ids[0]) : undefined;
    try {
      if (single) await downloadNode(single);
      else await downloadArchive(ids);
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
    <div className="flex h-full flex-col">
      {/* 헤더: breadcrumb + 액션 + 프로필 칩 */}
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          {/* 좁은 화면에서는 브레드크럼과 액션이 두 줄로 나뉜다(한 줄로 두면 업로드 버튼이 잘린다). */}
          <div className="flex flex-wrap items-center justify-between gap-3">
        <nav className="flex min-w-0 items-center gap-1 text-sm">
          {path.map((crumb, i) => {
            // 상위 경로는 드롭 대상이다 — 항목을 breadcrumb 에 떨어뜨려 한 단계 위로 꺼낸다.
            // 현재 폴더 자신과 "공유" 가상 폴더는 대상이 될 수 없다.
            const isCrumbDroppable =
              i < path.length - 1 && crumb.id !== SHARED_VIRTUAL_ID && canWrite;
            const isCrumbActive =
              dropTarget?.kind === "crumb" && dropTarget.id === crumb.id;
            return (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span className="text-muted">/</span>}
                <button
                  className={`truncate rounded px-1.5 py-0.5 ${
                    i === path.length - 1
                      ? "font-semibold"
                      : "text-muted hover:text-[color:var(--text-primary)]"
                  } ${isCrumbActive ? "bg-[color:var(--bg-muted)] ring-1 ring-[color:var(--accent)]" : ""}`}
                  onClick={() => goToCrumb(i)}
                  disabled={i === path.length - 1}
                  onDragOver={(e) => {
                    if (!isCrumbDroppable || !isInternalDrag(e)) return;
                    e.preventDefault();
                    setDropTarget({ kind: "crumb", id: crumb.id });
                  }}
                  onDragLeave={() => setDropTarget(null)}
                  onDrop={(e) => {
                    if (!isCrumbDroppable) return;
                    onDropOnFolder(e, crumb.id, crumb.name);
                  }}
                >
                  {crumb.name}
                </button>
              </span>
            );
          })}
        </nav>

        <div className="flex flex-wrap items-center gap-2">
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
              <button
                className="btn btn-secondary"
                onClick={() => folderInputRef.current?.click()}
              >
                <FolderUploadIcon width={16} height={16} />
                폴더 업로드
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
          {/*
            폴더 선택 전용 input — webkitdirectory 를 위 input 에 붙이면 개별 파일 선택이
            불가능해지므로 반드시 분리한다.
          */}
          <input
            ref={folderInputRef}
            type="file"
            multiple
            className="hidden"
            // @ts-expect-error - webkitdirectory 는 React 타입 정의에 없는 비표준 속성이다.
            webkitdirectory=""
            onChange={(e) => {
              if (e.target.files) planFolderUpload(collectFromInput(e.target.files));
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
        </PageHeader>

        {/*
          위키 켜진 폴더 경고 — 여기 올린 md·html 은 **올리는 즉시 전 구성원에게 공개**된다.
          업로드 버튼 바로 아래 상시 노출한다. 막지는 않는다 — 폴더 관리자가 의도해서 켠 것이고,
          올리는 사람에게 필요한 것은 금지가 아니라 "그럴 생각이 아니면 다른 폴더에 넣으라"는
          안내다. 소유자 승인 큐는 넣지 않기로 했다(spec 「폴더 상속 사고 경로」).
        */}
        {wikiFolder && canWrite && (
          <p
            className="mt-3 rounded-lg border px-3 py-2 text-xs"
            style={{ borderColor: "var(--warning)", color: "var(--warning)" }}
            role="status"
          >
            이 폴더는 <strong>전사 위키</strong>입니다. 여기 올리는 Markdown·HTML 문서는 올리는
            즉시 모든 구성원이 열람·다운로드할 수 있습니다. 공개할 문서가 아니면 다른 폴더에
            올려 주세요.
          </p>
        )}
      </div>

      {/* 본문: 드롭존 */}
      <div
        className="relative flex-1 overflow-auto p-6"
        // 드래그 앤 드롭 경로는 E2E 에서만 재현 가능하다(FileSystem API 는 합성 이벤트로만
        // 주입된다). 안정적인 드롭 대상 선택자를 남겨 둔다.
        data-testid="dropzone"
        onDragOver={(e) => {
          // 내부 드래그는 "현재 폴더로 이동" — 업로드 오버레이를 띄우지 않는다.
          if (isInternalDrag(e)) {
            if (canWrite) e.preventDefault();
            return;
          }
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
            <div className="text-center">
              <p className="font-medium text-accent">여기에 놓아 업로드</p>
              {/* 실제로 존재하는 제약만 알린다 — 배치 크기/개수는 내부 분할 사정이라 숨긴다. */}
              <p className="mt-1 text-xs text-muted">
                폴더도 가능 · 파일 1개 최대 10GB
                {Number.isFinite(remainingBytes) && ` · 남은 용량 ${formatBytes(remainingBytes)}`}
              </p>
            </div>
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
                    {/* 폴더 업로드 실패 목록 다시 보기 — 완료 직후 모달을 닫아도 사유를 확인할 수 있어야 한다. */}
                    {t.failures && t.failures.length > 0 && (
                      <button
                        className="btn btn-ghost px-2 py-0.5 text-xs"
                        onClick={() => setFolderFailures(t.failures ?? null)}
                      >
                        자세히
                      </button>
                    )}
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

        {/* 최근 항목 스트립 — 내 드라이브 루트에서만, 항목이 있을 때만 (Phase 8-3) */}
        {showRecentStrip && recent.length > 0 && (
          <div className="mb-5">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              최근 항목
            </h2>
            <div className="flex gap-3 overflow-x-auto pb-1">
              {recent.map((f) => (
                <button
                  key={f.id}
                  onClick={() => openPreview(f)}
                  title={f.name}
                  className="card flex w-32 shrink-0 flex-col items-center gap-2 p-3 text-center transition-colors hover:bg-[color:var(--bg-muted)]"
                >
                  <span className="flex h-12 w-12 items-center justify-center overflow-hidden rounded text-muted">
                    <Thumbnail
                      file={f}
                      className="h-12 w-12 rounded object-cover"
                      fallback={<FileIcon width={28} height={28} />}
                    />
                  </span>
                  <span className="w-full truncate text-xs">{f.name}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={reload} />
        ) : items.length === 0 && !showSharedVirtualRow ? (
          <EmptyState
            icon={<FolderIcon width={40} height={40} />}
            title={atVirtualShared ? "공유받은 항목이 없습니다" : "이 폴더가 비어 있습니다"}
            hint={
              atVirtualShared
                ? "그룹에 초대되고 파일 권한을 받으면 여기에 표시됩니다."
                : canWrite
                  ? "파일을 끌어다 놓거나 업로드 버튼을 눌러 시작하세요."
                  : "표시할 항목이 없습니다."
            }
          />
        ) : (
          (() => {
            const rowProps = {
              canWrite,
              canManage,
              onOpenFolder: openFolder,
              onPreview: openPreview,
              onDownload,
              onRename: (f: FileNode) => {
                focusItem(f.id);
                setRenamingId(f.id);
              },
              onShare: (f: FileNode) => setShareTarget(f),
              onPermissions: (f: FileNode) => setPermTarget(f),
              onWiki: (f: FileNode) => setWikiTarget(f),
              onDelete: (f: FileNode) => setDeleteTarget(f),
              onVersions: (f: FileNode) => setVersionsTarget(f),
              onNewVersion: startVersionUpload,
              onMove: (f: FileNode) => setMoveTarget(f),
              onToggleFavorite,
              // 드래그 이동은 "공유" 가상 목록처럼 컨테이너에 쓰기가 없는 곳에서는 끈다.
              dragEnabled: canWrite && !atVirtualShared,
              dropTargetId: dropTarget?.kind === "row" ? dropTarget.id : null,
              onDragStartItem: (f: FileNode, e: DragEvent) => {
                draggingRef.current = f;
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData(DRAG_MIME, String(f.id));
              },
              onDragEndItem: () => {
                draggingRef.current = null;
                setDropTarget(null);
              },
              onDragOverFolder: (f: FileNode, e: DragEvent) => {
                // 자기 자신 위로는 놓을 수 없다 — 하이라이트도 주지 않는다.
                if (!isInternalDrag(e) || draggingRef.current?.id === f.id) return;
                e.preventDefault();
                e.stopPropagation();
                e.dataTransfer.dropEffect = "move";
                setDropTarget({ kind: "row", id: f.id });
              },
              onDragLeaveFolder: () => setDropTarget(null),
              onDropOnFolderRow: (f: FileNode, e: DragEvent) => onDropOnFolder(e, f.id, f.name),
              // 내 드라이브 루트에서만 상단에 "공유" 가상 폴더 행을 고정한다.
              onOpenShared: showSharedVirtualRow ? openSharedVirtual : undefined,
              activeId,
              onFocus: focusItem,
              selectedIds,
              onSelect: selectItem,
              allSelected,
              onToggleSelectAll: toggleSelectAll,
              renamingId,
              onRenameSubmit: submitRename,
              onRenameCancel: () => setRenamingId(null),
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

        {/*
          선택 액션 바 — 체크한 항목이 있으면 목록 아래에 떠서 따라온다. 지금은 다운로드만
          싣는다 (여러 항목·폴더는 서버가 ZIP 하나로 묶어 스트리밍한다).
          목록 "위"가 아니라 "아래"에 두는 것이 핵심이다: 바가 생기면서 행이 밀리면 곧이어
          누르는 클릭이 옆 행에 떨어진다.
        */}
        {selectedIds.length > 0 && (
          <div className="pointer-events-none sticky bottom-0 z-20 flex justify-center pt-3">
            <div
              className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-full border border-token bg-[color:var(--bg-secondary)] px-4 py-2 text-sm shadow-lg"
              data-testid="selection-bar"
            >
              <span className="font-medium">{selectedIds.length}개 선택됨</span>
              <button className="btn btn-primary" onClick={() => void downloadSelection()}>
                <DownloadIcon width={16} height={16} />
                다운로드
              </button>
              <button className="btn btn-secondary" onClick={clearSelection}>
                선택 해제
              </button>
            </div>
          </div>
        )}
      </div>

      {/*
        폴더 업로드 사전 검사 — 바이트 하나 보내기 전에 요약과 건너뛸 항목을 보여준다.
        용량 부족일 때만 업로드를 막고, 그 밖의 위반은 해당 항목만 빼고 진행한다.
      */}
      <Modal
        open={folderPlan !== null}
        title="폴더 업로드"
        size="lg"
        onClose={() => setFolderPlan(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setFolderPlan(null)}>
              취소
            </button>
            <button
              className="btn btn-primary"
              disabled={!folderPlan || folderPlan.plan.entries.length === 0 || folderPlan.plan.quotaShortfall > 0}
              onClick={() => void startFolderUpload()}
            >
              {folderPlan ? `${folderPlan.plan.entries.length}개 업로드` : "업로드"}
            </button>
          </>
        }
      >
        {folderPlan && (
          <div className="space-y-3 text-sm">
            <p>
              파일 {folderPlan.plan.entries.length}개 · 폴더 {folderPlan.plan.dirs.length}개 · 총{" "}
              {formatBytes(folderPlan.plan.totalBytes)}
            </p>
            {folderPlan.plan.quotaShortfall > 0 ? (
              <p className="text-danger">
                남은 용량이 부족합니다 (남은 용량 {formatBytes(remainingBytes)}).{" "}
                {formatBytes(folderPlan.plan.quotaShortfall)}을 확보하거나 일부만 선택해 주세요.
              </p>
            ) : (
              Number.isFinite(remainingBytes) && (
                <p className="text-muted">
                  남은 용량 {formatBytes(remainingBytes)} → 업로드 후{" "}
                  {formatBytes(remainingBytes - folderPlan.plan.totalBytes)}
                </p>
              )
            )}
            {folderPlan.plan.skipped.length > 0 && (
              <div>
                <p className="font-medium">{folderPlan.plan.skipped.length}개를 건너뜁니다</p>
                <ul className="mt-1 max-h-48 space-y-1 overflow-auto text-muted">
                  {folderPlan.plan.skipped.map((s) => (
                    <li key={s.path}>
                      <span className="break-all">{s.path}</span> — {s.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 폴더 업로드 실패 목록 — 개별 토스트 대신 완료 후 한 번에 보여준다. */}
      <Modal
        open={folderFailures !== null}
        title="업로드하지 못한 항목"
        size="lg"
        onClose={() => setFolderFailures(null)}
        footer={
          <button className="btn btn-secondary" onClick={() => setFolderFailures(null)}>
            닫기
          </button>
        }
      >
        <ul className="max-h-80 space-y-1 overflow-auto text-sm text-muted">
          {(folderFailures ?? []).map((f) => (
            <li key={f.path}>
              <span className="break-all">{f.path}</span> — {f.reason}
            </li>
          ))}
        </ul>
      </Modal>

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

      {/* 이름 변경은 목록 안 제자리 편집(InlineNameInput)으로 처리한다 — 모달 없음 */}

      {/* 이동 대상 폴더 선택 */}
      <MoveModal
        target={moveTarget}
        currentParentId={parentId}
        onClose={() => setMoveTarget(null)}
        onMove={submitMove}
      />

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

      <WikiModal
        file={wikiTarget}
        open={wikiTarget !== null}
        onClose={() => setWikiTarget(null)}
        // 토글 결과(배지·권한)를 목록에 반영한다. 전사 공개는 권한 변경이라 그룹 컬럼도 바뀐다.
        onChanged={() => void reload()}
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
      <div className="flex h-full flex-col p-6">
        <ErrorState
          message="공유 폴더를 열 수 없습니다."
          onRetry={() => navigate("/", { replace: true })}
        />
      </div>
    );
  }

  if (name == null) {
    return (
      <div className="flex h-full flex-col">
        <LoadingState />
      </div>
    );
  }

  // 딥링크는 "내 드라이브 > 공유 > 폴더명" 아래에 seed 해 통합 트리 내비게이션과 이어지게 한다.
  return (
    <FileBrowserPage
      rootId={id}
      rootName={name}
      initialPath={[
        { id: null, name: "내 드라이브" },
        { id: SHARED_VIRTUAL_ID, name: "공유" },
        { id, name },
      ]}
    />
  );
}

/** 목록/그리드가 공유하는 행 동작 핸들러. */
interface FileRowProps {
  canWrite: boolean;
  canManage: boolean;
  onOpenFolder: (f: FileNode) => void;
  onPreview: (f: FileNode) => void;
  onDownload: (f: FileNode) => void;
  onRename: (f: FileNode) => void;
  onShare: (f: FileNode) => void;
  onPermissions: (f: FileNode) => void;
  onWiki: (f: FileNode) => void;
  onDelete: (f: FileNode) => void;
  onVersions: (f: FileNode) => void;
  onNewVersion: (f: FileNode) => void;
  onMove: (f: FileNode) => void;
  onToggleFavorite: (f: FileNode) => void;
  /** 지정 시 목록 상단에 "공유" 가상 폴더 행을 고정 노출한다(내 드라이브 루트 전용). */
  onOpenShared?: () => void;
  // --- 현재 항목 / 체크 선택 (클릭=현재 항목, 더블클릭=열기, 체크박스·Ctrl/Shift=체크) ---
  /** 클릭으로 잡힌 현재 항목. 하이라이트·F2 기준일 뿐 체크와는 무관하다. */
  activeId: number | null;
  onFocus: (id: number) => void;
  /** 체크된 항목 id 들 — 선택 액션 바의 대상. */
  selectedIds: number[];
  onSelect: (id: number, mods: SelectMods) => void;
  /** 목록 전체가 선택됐는지 (헤더 체크박스 상태). */
  allSelected: boolean;
  onToggleSelectAll: () => void;
  // --- 제자리 이름 편집 (F2 / 이름 재클릭 / 행 액션) ---
  /** 편집 중인 항목 id. 그 행만 이름 자리에 입력이 뜬다. */
  renamingId: number | null;
  onRenameSubmit: (f: FileNode, name: string) => void;
  onRenameCancel: () => void;
  // --- 드래그 이동 ---
  /** 항목을 끌 수 있는지(컨테이너에 쓰기 권한이 있을 때만). */
  dragEnabled: boolean;
  /** 현재 드롭 하이라이트 중인 폴더 행 id. */
  dropTargetId: number | null;
  onDragStartItem: (f: FileNode, e: DragEvent) => void;
  onDragEndItem: () => void;
  onDragOverFolder: (f: FileNode, e: DragEvent) => void;
  onDragLeaveFolder: () => void;
  onDropOnFolderRow: (f: FileNode, e: DragEvent) => void;
}

/** 권한 셀 — owner 는 "소유자"로 구분해 강조하고, 그 외는 라벨/톤 배지로 표시. */
function PermissionCell({ f }: { f: FileNode }) {
  if (!f.permission) return <span className="text-muted">-</span>;
  if (f.permission === "owner") {
    // nowrap: 이름 열이 넓어지면 이 칸이 먼저 좁아져 "소유 / 자" 로 접혔다.
    return <span className="whitespace-nowrap text-xs font-medium text-accent">소유자</span>;
  }
  return <Badge tone={permissionTone(f.permission)}>{permissionLabel(f.permission)}</Badge>;
}

/** 그룹명 목록을 ", " 로 합쳐 표시하고, 비어 있으면 "-". */
function groupText(f: FileNode): string {
  return f.group_names && f.group_names.length > 0 ? f.group_names.join(", ") : "-";
}

/**
 * 행별 액션 가드는 컨테이너 perm 이 아니라 항목 자체의 유효 권한(f.permission)으로 판정한다.
 * "owner"는 permissionCovers 순위표에 없어 별도로 전부 허용한다. f.permission 이 없으면
 * (백엔드가 아직 안 채운 경우) 컨테이너 수준 값으로 fallback 한다.
 */
function rowCanWrite(f: FileNode, containerCanWrite: boolean): boolean {
  if (!f.permission) return containerCanWrite;
  return f.permission === "owner" || permissionCovers(f.permission, "write");
}

function rowCanManage(f: FileNode, containerCanManage: boolean): boolean {
  if (!f.permission) return containerCanManage;
  return f.permission === "owner" || permissionCovers(f.permission, "manage");
}

/**
 * 행/카드의 선택 체크박스. 행 클릭 판정(선택/열기)에서 빠지도록 ROW_ACTION_PROPS 를 두른다.
 * 체크박스는 언제나 "누적 토글"이라 Ctrl 클릭과 같은 뜻으로 넘긴다.
 */
function SelectCheckbox({
  checked,
  label,
  onToggle,
  className,
}: {
  checked: boolean;
  label: string;
  onToggle: () => void;
  className?: string;
}) {
  return (
    <span className={className} {...ROW_ACTION_PROPS}>
      <input type="checkbox" checked={checked} aria-label={label} onChange={onToggle} />
    </span>
  );
}

/** 파일/폴더 한 항목의 아이콘 동작 모음 (목록·그리드 공용). */
function RowActions({ file: f, ...p }: { file: FileNode } & FileRowProps) {
  // 행별 조작(새 버전/이름 변경/삭제/권한 관리)은 항목 자체 권한으로 게이팅한다.
  const canWrite = rowCanWrite(f, p.canWrite);
  const canManage = rowCanManage(f, p.canManage);
  return (
    <>
      {f.is_folder && (
        <>
          {/* 폴더는 하위 전체를 ZIP 하나로 묶어 받는다 (backend 스트리밍). */}
          <IconAction title="ZIP 으로 다운로드" onClick={() => p.onDownload(f)}>
            <DownloadIcon width={16} height={16} />
          </IconAction>
          {/* 폴더 공유 — 방문자는 하위 전체를 ZIP 으로 받는다. 파일과 같은 기준(write 이상). */}
          {canWrite && (
            <IconAction title="공유" onClick={() => p.onShare(f)}>
              <ShareIcon width={16} height={16} />
            </IconAction>
          )}
        </>
      )}
      {!f.is_folder && (
        <>
          <IconAction title="미리보기" onClick={() => p.onPreview(f)}>
            <EyeIcon width={16} height={16} />
          </IconAction>
          <IconAction title="다운로드" onClick={() => p.onDownload(f)}>
            <DownloadIcon width={16} height={16} />
          </IconAction>
          {canWrite && (
            <IconAction title="새 버전 업로드" onClick={() => p.onNewVersion(f)}>
              <UploadIcon width={16} height={16} />
            </IconAction>
          )}
          <IconAction title="버전 기록" onClick={() => p.onVersions(f)}>
            <HistoryIcon width={16} height={16} />
          </IconAction>
          {/* 공유 링크 생성은 소유자 또는 write 이상 — 백엔드 create_share 와 같은 기준으로
              게이팅한다(컨테이너가 공유 트리인지가 아니라 항목 자체의 유효 권한). */}
          {canWrite && (
            <IconAction title="공유" onClick={() => p.onShare(f)}>
              <ShareIcon width={16} height={16} />
            </IconAction>
          )}
        </>
      )}
      {canManage && (
        <>
          <IconAction title="권한 관리" onClick={() => p.onPermissions(f)}>
            <ShieldIcon width={16} height={16} />
          </IconAction>
          {/* 발행은 소유자·manage 만 — 백엔드 _can_publish 와 같은 기준으로 게이팅한다.
              md/html 이 아닌 파일에서도 연다. 모달이 스위치를 비활성 + 사유로 보여주기 때문이다 —
              숨기면 "기능이 없다"로 읽히고, 실제로는 "이 형식은 아직 안 된다"다. */}
          <IconAction title="위키 설정" onClick={() => p.onWiki(f)}>
            <BookIcon width={16} height={16} />
          </IconAction>
        </>
      )}
      {canWrite && (
        <>
          <IconAction title="이동" onClick={() => p.onMove(f)}>
            <MoveIcon width={16} height={16} />
          </IconAction>
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

/**
 * 항목의 이름 표시 — 편집 중이면 같은 자리에 입력이 뜬다.
 * 표시 상태에서는 클릭 판정을 행이 맡으므로, 이 버튼은 키보드 포커스/Enter 진입점일 뿐이다.
 * 이름 변경은 행 액션의 "이름 변경" 아이콘(또는 F2)으로만 들어간다.
 */
function ItemName({
  file: f,
  thumb,
  children,
  className,
  ...p
}: {
  file: FileNode;
  /** 편집 중에도 그대로 남는 썸네일/아이콘. */
  thumb: React.ReactNode;
  /** 표시 상태의 이름 영역 (이름 + 배지 등). */
  children: React.ReactNode;
  className: string;
} & FileRowProps) {
  if (p.renamingId === f.id) {
    // 편집 중에는 입력이 남는 폭을 다 쓰도록 flex-1 을 더한다.
    return (
      <div className={`${className} flex-1`}>
        {thumb}
        <InlineNameInput
          file={f}
          onSubmit={(name) => p.onRenameSubmit(f, name)}
          onCancel={p.onRenameCancel}
        />
      </div>
    );
  }
  return (
    <button
      className={className}
      title={f.is_folder ? "더블클릭하면 폴더를 엽니다" : "더블클릭하면 미리봅니다"}
    >
      {thumb}
      {children}
    </button>
  );
}

function FileTable({ items, ...p }: { items: FileNode[] } & FileRowProps) {
  const coarse = useCoarsePointer();
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-token text-left text-xs text-muted">
            <th className="w-10 px-4 py-2.5">
              <SelectCheckbox
                checked={p.allSelected}
                label="전체 선택"
                onToggle={p.onToggleSelectAll}
              />
            </th>
            <th className="px-4 py-2.5 font-medium">이름</th>
            <th className="w-32 px-4 py-2.5 font-medium">소유자</th>
            <th className="w-40 px-4 py-2.5 font-medium">그룹</th>
            <th className="w-24 px-4 py-2.5 font-medium">권한</th>
            <th className="w-24 px-4 py-2.5 font-medium">크기</th>
            <th className="w-36 px-4 py-2.5 font-medium">수정일</th>
            <th className="w-64 px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {/* 내 드라이브 루트 전용 "공유" 가상 폴더 고정 행 */}
          {p.onOpenShared && (
            <tr
              className={`group border-b border-token hover:bg-[color:var(--bg-muted)] ${ROW_BASE_CLASS} ${
                p.activeId === SHARED_VIRTUAL_ID ? ROW_ACTIVE_CLASS : ""
              }`}
              // 내려받을 실물이 없어 체크 대상이 아니다 — 현재 항목 표시까지만 한다.
              {...rowOpenHandlers({
                coarse,
                focus: () => p.onFocus(SHARED_VIRTUAL_ID),
                open: p.onOpenShared,
              })}
            >
              {/* 가상 폴더는 내려받을 실물이 없어 선택 체크박스를 두지 않는다. */}
              <td className="px-4 py-2.5" />
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-accent">
                    <InboxIcon width={20} height={20} />
                  </span>
                  <span className="font-medium">공유</span>
                  <Badge tone="accent">공유받은 항목</Badge>
                </div>
              </td>
              <td className="px-4 py-2.5 text-muted">-</td>
              <td className="px-4 py-2.5 text-muted">-</td>
              <td className="px-4 py-2.5 text-muted">-</td>
              <td className="px-4 py-2.5 text-muted">-</td>
              <td className="px-4 py-2.5 text-muted">-</td>
              <td className="px-4 py-2.5" />
            </tr>
          )}
          {items.map((f) => (
            <tr
              key={f.id}
              className={`group border-b border-token last:border-0 hover:bg-[color:var(--bg-muted)] ${ROW_BASE_CLASS} ${
                p.dropTargetId === f.id
                  ? "bg-[color:var(--bg-muted)] outline outline-2 -outline-offset-2 outline-[color:var(--accent)]"
                  : p.selectedIds.includes(f.id)
                    ? ROW_SELECTED_CLASS
                    : p.activeId === f.id
                      ? ROW_ACTIVE_CLASS
                      : ""
              }`}
              {...rowOpenHandlers({
                coarse,
                focus: () => p.onFocus(f.id),
                select: (mods) => p.onSelect(f.id, mods),
                open: () => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f)),
              })}
              // 편집 중에는 글자를 긁는 동작이 드래그 이동으로 새지 않게 끈다.
              draggable={p.dragEnabled && p.renamingId !== f.id && rowCanWrite(f, p.canWrite)}
              onDragStart={(e) => p.onDragStartItem(f, e)}
              onDragEnd={p.onDragEndItem}
              onDragOver={f.is_folder ? (e) => p.onDragOverFolder(f, e) : undefined}
              onDragLeave={f.is_folder ? p.onDragLeaveFolder : undefined}
              onDrop={f.is_folder ? (e) => p.onDropOnFolderRow(f, e) : undefined}
            >
              <td className="px-4 py-2.5">
                <SelectCheckbox
                  checked={p.selectedIds.includes(f.id)}
                  label={`${f.name} 선택`}
                  onToggle={() => p.onSelect(f.id, { toggle: true, range: false })}
                />
              </td>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <ItemName
                    file={f}
                    className="flex min-w-0 items-center gap-2.5 text-left"
                    thumb={
                      /* 이미지면 미니 썸네일, 아니면 유형 아이콘 */
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
                    }
                    {...p}
                  >
                    {/* 위키 표식(책) — 폴더·파일 공통. 이름 앞 윗첨자 자리라 별도 컬럼이 필요 없고,
                        표식이 세로로 정렬돼 목록을 훑을 때 한눈에 걸린다. */}
                    <span className="truncate">
                      <WikiMark file={f} />
                      <span className={f.is_folder ? "font-medium" : ""}>{f.name}</span>
                    </span>
                    {!f.is_folder && f.current_version >= 2 && (
                      <Badge tone="neutral">v{f.current_version}</Badge>
                    )}
                  </ItemName>
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
                  className="flex justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100"
                  {...ROW_ACTION_PROPS}
                >
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

/** 썸네일 중심 그리드 뷰 (PRD 3.2). 클릭은 현재 항목, 더블클릭은 폴더 탐색/파일 미리보기. */
function FileGrid({ items, ...p }: { items: FileNode[] } & FileRowProps) {
  const coarse = useCoarsePointer();
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
      {/* 내 드라이브 루트 전용 "공유" 가상 폴더 카드 */}
      {p.onOpenShared && (
        <button
          {...rowOpenHandlers({
            coarse,
            // 내려받을 실물이 없어 체크 대상이 아니다 — 현재 항목 표시까지만 한다.
            focus: () => p.onFocus(SHARED_VIRTUAL_ID),
            open: p.onOpenShared,
          })}
          className={`card relative flex flex-col overflow-hidden p-0 text-left transition-colors hover:bg-[color:var(--bg-muted)] ${ROW_BASE_CLASS} ${
            p.activeId === SHARED_VIRTUAL_ID ? CARD_ACTIVE_CLASS : ""
          }`}
          title="공유받은 항목"
        >
          <span className="flex aspect-square w-full items-center justify-center bg-muted-token text-accent">
            <InboxIcon width={44} height={44} />
          </span>
          <div className="flex flex-col gap-1 border-t border-token px-2.5 py-2">
            <p className="truncate text-xs font-medium">공유</p>
            <p className="text-[10px] text-muted">공유받은 항목</p>
          </div>
        </button>
      )}
      {items.map((f) => (
        <div
          key={f.id}
          className={`group card relative flex flex-col overflow-hidden p-0 ${ROW_BASE_CLASS} ${
            p.dropTargetId === f.id
              ? "ring-2 ring-[color:var(--accent)]"
              : p.selectedIds.includes(f.id)
                ? CARD_SELECTED_CLASS
                : p.activeId === f.id
                  ? CARD_ACTIVE_CLASS
                  : ""
          }`}
          {...rowOpenHandlers({
            coarse,
            focus: () => p.onFocus(f.id),
            select: (mods) => p.onSelect(f.id, mods),
            open: () => (f.is_folder ? p.onOpenFolder(f) : p.onPreview(f)),
          })}
          // 편집 중에는 글자를 긁는 동작이 드래그 이동으로 새지 않게 끈다.
          draggable={p.dragEnabled && p.renamingId !== f.id && rowCanWrite(f, p.canWrite)}
          onDragStart={(e) => p.onDragStartItem(f, e)}
          onDragEnd={p.onDragEndItem}
          onDragOver={f.is_folder ? (e) => p.onDragOverFolder(f, e) : undefined}
          onDragLeave={f.is_folder ? p.onDragLeaveFolder : undefined}
          onDrop={f.is_folder ? (e) => p.onDropOnFolderRow(f, e) : undefined}
        >
          {/* 선택 체크박스 — 선택됐으면 항상, 아니면 hover 시 노출 */}
          <SelectCheckbox
            checked={p.selectedIds.includes(f.id)}
            label={`${f.name} 선택`}
            onToggle={() => p.onSelect(f.id, { toggle: true, range: false })}
            className={`absolute left-1.5 top-1.5 z-10 transition-opacity ${
              p.selectedIds.includes(f.id) ? "" : "opacity-0 group-hover:opacity-100"
            }`}
          />
          {/* 즐겨찾기 별 — 활성이면 항상, 아니면 hover 시 노출 */}
          <span className="absolute right-1.5 top-1.5 z-10" {...ROW_ACTION_PROPS}>
            <FavoriteStar active={f.is_favorite} onToggle={() => p.onToggleFavorite(f)} />
          </span>
          {/* 썸네일/아이콘 영역 (정사각형) — 클릭 판정은 카드가 맡는다 */}
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
            {/* 그리드는 간결 유지 — 소유자가 아닌 항목만 작은 권한 배지 노출 */}
            {f.permission && f.permission !== "owner" && (
              <span className="absolute bottom-1.5 left-1.5">
                <Badge tone={permissionTone(f.permission)}>{permissionLabel(f.permission)}</Badge>
              </span>
            )}
          </button>

          {/* 이름 + 동작 */}
          <div className="flex flex-col gap-1 border-t border-token px-2.5 py-2">
            <ItemName file={f} className="min-w-0 text-left" thumb={null} {...p}>
              {/* 목록과 같은 자리(이름 앞 윗첨자)에 둔다 — 보기를 바꿔도 표식이 옮겨 다니지 않는다. */}
              <p className={`truncate text-xs ${f.is_folder ? "font-medium" : ""}`}>
                <WikiMark file={f} />
                {f.name}
              </p>
            </ItemName>
            <p className="text-[10px] text-muted">{f.is_folder ? "폴더" : formatBytes(f.size)}</p>
            {/* 좁은 셀에서 넘치지 않도록 아이콘은 줄바꿈 허용 */}
            <div
              className="flex flex-wrap opacity-0 transition-opacity group-hover:opacity-100"
              {...ROW_ACTION_PROPS}
            >
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
        danger
          ? "text-muted hover:text-danger"
          : "text-muted hover:text-[color:var(--text-primary)]"
      }`}
    >
      {children}
    </button>
  );
}
