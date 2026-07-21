/**
 * 폴더 업로드용 파일 트리 수집 + 경로 검증.
 *
 * 두 입구(폴더 선택 input, 드래그 앤 드롭)가 서로 다른 브라우저 API 를 쓰지만 산출물은
 * 같다 — `{ files: [{path, file}], dirs: string[] }`.
 *
 * 경로 규칙은 백엔드 `app/services/files.py: normalize_relpath` 와 **반드시 일치**해야 한다.
 * 어긋나면 클라이언트가 통과시킨 항목을 서버가 거부해, 사전 검사를 통과한 업로드가
 * 뒤늦게 실패한다. 규칙을 바꿀 때는 양쪽을 함께 고쳐야 한다.
 */

export const MAX_PATH_DEPTH = 32;
export const MAX_PATH_LENGTH = 4096;
export const MAX_NAME_LENGTH = 255;

export interface CollectedFile {
  /** 파일명을 포함한 상대 경로 ("docs/img/a.png"). */
  path: string;
  file: File;
}

export interface CollectedTree {
  files: CollectedFile[];
  /** 트리에 등장하는 모든 디렉터리 경로(빈 폴더 포함). */
  dirs: string[];
}

/**
 * 상대 경로를 세그먼트로 정규화한다. 규칙 위반 시 사용자에게 보여줄 사유를 문자열로 던진다.
 * 성공 시 세그먼트 배열을 반환한다.
 */
export function normalizeRelPath(raw: string): { segments: string[] } | { error: string } {
  if (raw.length > MAX_PATH_LENGTH) return { error: "경로가 너무 깁니다" };

  const unified = raw.replace(/\\/g, "/");
  if (unified.startsWith("/")) return { error: "절대 경로는 사용할 수 없습니다" };
  const head = unified.split("/", 1)[0] ?? "";
  if (head.length >= 2 && head[1] === ":") return { error: "절대 경로는 사용할 수 없습니다" };

  const segments: string[] = [];
  for (const rawSeg of unified.split("/")) {
    if (rawSeg === "" || rawSeg === ".") continue;
    // ".." 만 탈출이다. "..foo"/".hidden" 은 정상 이름이므로 통과시킨다.
    if (rawSeg === "..") return { error: "상위 경로(..)는 사용할 수 없습니다" };
    const seg = rawSeg.trim();
    if (!seg) return { error: "이름이 비어 있습니다" };
    if (seg.length > MAX_NAME_LENGTH) return { error: "이름이 255자를 넘습니다" };
    // eslint-disable-next-line no-control-regex
    if (/[\x00-\x1f\x7f]/.test(seg)) return { error: "이름에 쓸 수 없는 문자가 있습니다" };
    segments.push(seg);
  }

  if (segments.length === 0) return { error: "경로가 비어 있습니다" };
  if (segments.length > MAX_PATH_DEPTH) return { error: "폴더 깊이가 32단계를 넘습니다" };
  return { segments };
}

/** 파일 경로들에서 조상 디렉터리 경로를 전부 뽑아 중복 없이 반환한다. */
export function ancestorDirs(paths: string[]): string[] {
  const out = new Set<string>();
  for (const p of paths) {
    const segments = p.split("/");
    segments.pop(); // 파일명 제거
    for (let i = 1; i <= segments.length; i += 1) out.add(segments.slice(0, i).join("/"));
  }
  return [...out];
}

// --- 입구 1: <input webkitdirectory> ----------------------------------------

/**
 * 폴더 선택 input 의 FileList 를 트리로 변환한다.
 * `webkitRelativePath` 가 이미 "선택폴더/docs/a.png" 형태의 상대 경로다.
 * 개별 파일 선택으로 들어오면 빈 문자열이라 파일명으로 대체한다.
 */
export function collectFromInput(list: FileList): CollectedTree {
  const files: CollectedFile[] = [];
  for (const file of Array.from(list)) {
    files.push({ path: file.webkitRelativePath || file.name, file });
  }
  // input 경로로는 빈 폴더를 알 수 없다 — 브라우저가 파일만 열거하기 때문.
  return { files, dirs: ancestorDirs(files.map((f) => f.path)) };
}

// --- 입구 2: 드래그 앤 드롭 (FileSystem API) --------------------------------

interface FsEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file(cb: (f: File) => void, err: (e: unknown) => void): void;
  createReader(): FsDirectoryReader;
}

interface FsDirectoryReader {
  readEntries(cb: (entries: FsEntry[]) => void, err: (e: unknown) => void): void;
}

const entryFile = (entry: FsEntry) =>
  new Promise<File>((resolve, reject) => entry.file(resolve, reject));

/**
 * 디렉터리의 전체 항목을 읽는다.
 *
 * `readEntries` 는 한 번에 최대 100개만 돌려주므로 **빈 배열이 올 때까지 반복 호출**해야
 * 한다. 한 번만 부르면 101번째부터 조용히 사라진다.
 */
async function readAllEntries(reader: FsDirectoryReader): Promise<FsEntry[]> {
  const all: FsEntry[] = [];
  for (;;) {
    const batch = await new Promise<FsEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

async function walkEntry(entry: FsEntry, prefix: string, out: CollectedTree): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    out.files.push({ path, file: await entryFile(entry) });
    return;
  }
  if (!entry.isDirectory) return;

  out.dirs.push(path); // 빈 폴더도 여기서 기록된다.
  const children = await readAllEntries(entry.createReader());
  for (const child of children) await walkEntry(child, path, out);
}

/**
 * 드롭된 항목을 트리로 변환한다.
 *
 * 호출자는 **동기적으로** `dataTransfer.items` 를 배열로 복사해 넘겨야 한다 —
 * `dataTransfer` 는 이벤트 핸들러가 반환되면 무효화되므로, await 뒤에 접근하면 비어 있다.
 */
export async function collectFromEntries(entries: unknown[]): Promise<CollectedTree> {
  const out: CollectedTree = { files: [], dirs: [] };
  for (const entry of entries) {
    if (entry) await walkEntry(entry as FsEntry, "", out);
  }
  // 파일만 드롭한 경우에도 조상 경로를 보태 dirs 를 완전하게 만든다.
  const dirs = new Set([...out.dirs, ...ancestorDirs(out.files.map((f) => f.path))]);
  return { files: out.files, dirs: [...dirs] };
}

/** 드롭 이벤트에서 FileSystemEntry 들을 동기적으로 뽑아낸다. */
export function entriesFromDrop(dt: DataTransfer): unknown[] {
  const items = Array.from(dt.items);
  return items
    .map((it) => (it.kind === "file" ? it.webkitGetAsEntry?.() : null))
    .filter(Boolean);
}

/** 드롭된 항목에 디렉터리가 하나라도 있는지 — 폴더 업로드 경로로 보낼지 판단한다. */
export function dropHasDirectory(entries: unknown[]): boolean {
  return entries.some((e) => (e as FsEntry).isDirectory);
}
