/**
 * BeeMate PC 수신 — 클립보드 쓰기, 저장 폴더(File System Access API), 기기 id 보관.
 * spec/beemate.md 「PC 수신 화면」.
 *
 * 브라우저 제약이 설계를 정한다.
 *  - 클립보드 이미지: Chrome 은 `image/png` 만 받는다 → 다른 형식은 캔버스로 PNG 변환.
 *    그리고 **문서에 포커스가 있을 때만** 쓸 수 있다 → 호출부가 포커스를 기다렸다 쓴다.
 *  - 폴더 저장: showDirectoryPicker 는 Chrome·Edge 만 있다. 핸들은 IndexedDB 에 둘 수 있지만
 *    권한은 브라우저를 다시 켜면 "prompt" 로 돌아갈 수 있고, 다시 허용은 사용자 제스처가 필요하다.
 *    폴더가 없거나 지원하지 않으면 일반 다운로드(브라우저 다운로드 폴더)로 떨어진다.
 */

// --- 기기 id (이 브라우저 = 기기 하나) ----------------------------------------

const DEVICE_KEY = "beemate.deviceId";

export function loadDeviceId(userId: number): number | null {
  try {
    const raw = localStorage.getItem(`${DEVICE_KEY}.${userId}`);
    const id = raw ? Number(raw) : NaN;
    return Number.isFinite(id) ? id : null;
  } catch {
    return null;
  }
}

export function saveDeviceId(userId: number, id: number | null): void {
  try {
    if (id == null) localStorage.removeItem(`${DEVICE_KEY}.${userId}`);
    else localStorage.setItem(`${DEVICE_KEY}.${userId}`, String(id));
  } catch {
    /* 저장 불가 환경이면 다음 방문 때 기기를 다시 등록한다 */
  }
}

// --- 클립보드 -----------------------------------------------------------------

export function isImage(mime: string): boolean {
  return mime.startsWith("image/") && mime !== "image/svg+xml";
}

async function toPng(blob: Blob): Promise<Blob> {
  if (blob.type === "image/png") return blob;
  const bitmap = await createImageBitmap(blob);
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("캔버스를 만들 수 없습니다.");
    ctx.drawImage(bitmap, 0, 0);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG 변환 실패"))), "image/png"),
    );
  } finally {
    bitmap.close();
  }
}

/** 이미지를 클립보드에 쓴다. 포커스가 없으면 브라우저가 거절하므로 호출부가 먼저 확인한다. */
export async function copyImageToClipboard(blob: Blob): Promise<void> {
  const png = await toPng(blob);
  await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
}

// --- 저장 폴더 ------------------------------------------------------------------

// File System Access API — TS 기본 lib(DOM)에 아직 일부가 없어 쓰는 만큼만 선언한다.
type PermissionMode = { mode: "readwrite" };
interface DirHandle extends FileSystemDirectoryHandle {
  queryPermission(d: PermissionMode): Promise<PermissionState>;
  requestPermission(d: PermissionMode): Promise<PermissionState>;
}
type WindowWithPicker = Window & {
  showDirectoryPicker?: (o?: { id?: string; mode?: "readwrite" }) => Promise<DirHandle>;
};

export function folderPickerSupported(): boolean {
  return typeof (window as WindowWithPicker).showDirectoryPicker === "function";
}

const DB_NAME = "beemate";
const STORE = "kv";
const FOLDER_KEY = "folder";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idb<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const req = run(db.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result as T);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

export type FolderState =
  | { kind: "unsupported" }
  | { kind: "none" }
  | { kind: "needs-permission"; name: string }
  | { kind: "ready"; name: string };

export async function loadFolder(): Promise<{ handle: DirHandle | null; state: FolderState }> {
  if (!folderPickerSupported()) return { handle: null, state: { kind: "unsupported" } };
  let handle: DirHandle | undefined;
  try {
    handle = await idb<DirHandle | undefined>("readonly", (s) => s.get(FOLDER_KEY));
  } catch {
    handle = undefined;
  }
  if (!handle) return { handle: null, state: { kind: "none" } };
  const perm = await handle.queryPermission({ mode: "readwrite" });
  return {
    handle,
    state: perm === "granted" ? { kind: "ready", name: handle.name } : { kind: "needs-permission", name: handle.name },
  };
}

/** 폴더 고르기 — 사용자 제스처(클릭) 안에서 불러야 한다. */
export async function pickFolder(): Promise<DirHandle> {
  const picker = (window as WindowWithPicker).showDirectoryPicker;
  if (!picker) throw new Error("이 브라우저는 폴더 저장을 지원하지 않습니다.");
  const handle = await picker({ id: "beemate", mode: "readwrite" });
  await idb("readwrite", (s) => s.put(handle, FOLDER_KEY));
  return handle;
}

/** 브라우저 재시작 뒤 "prompt" 로 돌아간 권한을 다시 받는다 — 사용자 제스처 안에서. */
export async function regrantFolder(handle: DirHandle): Promise<boolean> {
  return (await handle.requestPermission({ mode: "readwrite" })) === "granted";
}

export async function forgetFolder(): Promise<void> {
  await idb("readwrite", (s) => s.delete(FOLDER_KEY));
}

/** 같은 이름이 있으면 "name (1).ext" 로 비켜 간다 — 덮어쓰지 않는다. */
async function freeName(dir: DirHandle, name: string): Promise<string> {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : "";
  for (let i = 0; i < 1000; i += 1) {
    const candidate = i === 0 ? name : `${stem} (${i})${ext}`;
    try {
      await dir.getFileHandle(candidate);
    } catch {
      return candidate; // NotFoundError — 비어 있다
    }
  }
  return `${stem} (${Date.now()})${ext}`;
}

/** 폴더에 저장하고 실제로 쓴 파일명을 돌려준다. */
export async function saveToFolder(dir: DirHandle, name: string, blob: Blob): Promise<string> {
  const finalName = await freeName(dir, name);
  const fh = await dir.getFileHandle(finalName, { create: true });
  const w = await fh.createWritable();
  try {
    await w.write(blob);
  } finally {
    await w.close();
  }
  return finalName;
}

/** 폴더가 없을 때 — 브라우저 다운로드 폴더로 내려받는다. */
export function saveAsDownload(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export type { DirHandle };
