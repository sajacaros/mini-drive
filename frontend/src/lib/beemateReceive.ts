/**
 * BeeMate PC 수신 — 클립보드 쓰기, 저장 폴더(File System Access API), 기기 id 보관.
 * spec/beemate.md 「PC 수신 화면」.
 *
 * 브라우저 제약이 설계를 정한다.
 *  - 클립보드 이미지: Chrome 은 `image/png` 만 받는다 → 다른 형식은 캔버스로 PNG 변환.
 *    그리고 **문서에 포커스가 있을 때만** 쓸 수 있다 → 호출부가 포커스를 기다렸다 쓴다.
 *  - 폴더 저장: showDirectoryPicker 는 Chrome·Edge 만 있다. 핸들은 IndexedDB 에 둘 수 있지만
 *    권한은 브라우저를 다시 켜면 "prompt" 로 돌아갈 수 있고, 다시 허용은 사용자 제스처가 필요하다.
 *    폴더가 없거나 지원하지 않으면 일반 다운로드(브라우저 다운로드 폴더)로 떨어진다 — 이것이
 *    기본값이라 아무 설정 없이도 받는다.
 */

// --- 이 브라우저의 기기 이름표 -------------------------------------------------
//
// 등록 화면이 없다. 처음 붙을 때 만든 client_id 를 매 연결에 실어 보내면 서버가 같은 기기로
// 찾아 쓰거나 새로 만든다(spec/beemate.md 「기기 — 자동 등록」). 계정마다 따로 둘 필요는 없다
// — 서버의 유일성이 (사용자, client_id) 라 같은 값이 계정별로 다른 기기가 된다.

const CLIENT_KEY = "beemate.clientId";
let memoryClientId: string | null = null;

function newClientId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function getClientId(): string {
  try {
    const saved = localStorage.getItem(CLIENT_KEY);
    if (saved) return saved;
    const id = newClientId();
    localStorage.setItem(CLIENT_KEY, id);
    return id;
  } catch {
    // 저장 불가(사생활 보호 모드 등) — 이 탭이 살아 있는 동안만 같은 기기로 남는다.
    memoryClientId ??= newClientId();
    return memoryClientId;
  }
}

/** 처음 등록될 때 쓸 이름 — "Windows · Chrome". 나중에 기기 관리에서 바꾼다. */
export function defaultDeviceName(): string {
  const ua = navigator.userAgent;
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Mac OS X/.test(ua)
      ? "Mac"
      : /Android/.test(ua)
        ? "Android"
        : /iPhone|iPad/.test(ua)
          ? "iOS"
          : /Linux/.test(ua)
            ? "Linux"
            : "PC";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Whale\//.test(ua)
      ? "Whale"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Firefox\//.test(ua)
          ? "Firefox"
          : /Safari\//.test(ua)
            ? "Safari"
            : "브라우저";
  return `${os} · ${browser}`;
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
