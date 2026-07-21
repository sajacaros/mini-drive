/**
 * 폴더 업로드 오케스트레이션 — 사전 검사 · 2단계 전송 · 크기 분기 · 진행률 집계.
 *
 * 전송 경로는 파일 크기로 갈린다. 배치는 "작은 파일이 많을 때 요청 수를 줄이는" 수단이지
 * 모든 업로드의 새 경로가 아니다.
 *
 *   ≤ 64MB   배치 (여러 개를 한 요청에)
 *   ≤ 1GB    기존 단일 업로드 — 파일 하나가 이미 요청 하나 값을 해 묶을 이득이 없다
 *   그 이상   기존 재개 가능 업로드 — 청킹/일시정지/재개가 필요한 구간
 *
 * 큰 파일도 폴더 트리 안 제자리에 올라가야 하므로, 1단계에서 폴더만 먼저 만들어
 * 경로 → 폴더 id 맵을 받고 세 경로가 그 맵을 공유한다.
 */

import { errorStatus, retryAfterSeconds } from "@/api/client";
import { batchUpload, uploadFile } from "@/api/files";

import { normalizeRelPath, type CollectedFile, type CollectedTree } from "./fileTree";
import { formatBytes } from "./format";
import { RESUMABLE_THRESHOLD, startNewResumableUpload } from "./resumable";
import { uploadErrorFromException, uploadErrorMessage } from "./uploadError";

/**
 * 서버 `max_batch_bytes` — **multipart 본문 전체**의 상한이다.
 * 서버는 Content-Length 로 재므로 파일 내용뿐 아니라 경계·헤더·paths 필드가 전부 포함된다.
 */
export const BATCH_MAX_BYTES = 64 * 1024 * 1024;

/**
 * 배치에 담을 **파일 내용 합계**의 상한. 본문 상한보다 반드시 작아야 한다.
 *
 * 이 여유를 두지 않으면 내용 합계를 정확히 64MB 로 채운 배치가 오버헤드(파일 200개면 40KB
 * 남짓) 때문에 본문 상한을 넘겨 413 이 되고, 그 배치의 파일 **전부**가 한 번에 실패한다.
 * 실제로 이 버그로 한 번에 160건이 실패했다. 오버헤드는 파일 수와 경로 길이에 비례하므로
 * groupBatches 가 항목별로 추정치를 더해 계산하고, 여기에 더해 아래 여유분을 남긴다.
 */
export const BATCH_MAX_CONTENT_BYTES = 56 * 1024 * 1024;

/**
 * multipart 항목 1개의 고정 오버헤드 추정치(경계 + Content-Disposition/Type 헤더 + CRLF).
 * 파일 파트와 paths 파트 두 개 몫이며, 가변 부분(파일명·경로)은 별도로 더한다.
 */
const MULTIPART_ENTRY_OVERHEAD = 400;

/** 한 배치의 파일 개수 상한. 서버 `max_batch_files` 와 같은 값이어야 한다. */
export const BATCH_MAX_FILES = 200;
/** 파일 1개 크기 상한 (서버 MAX_FILE_SIZE). */
export const MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024;

const BATCH_CONCURRENCY = 2;
const RATE_LIMIT_RETRIES = 3;

export interface SkippedEntry {
  path: string;
  reason: string;
}

export interface PreflightResult {
  /** 업로드 대상. path 는 정규화된 상대 경로다. */
  entries: CollectedFile[];
  /** 정규화된 디렉터리 경로 전체. */
  dirs: string[];
  /** 규칙 위반으로 건너뛸 항목과 사유. */
  skipped: SkippedEntry[];
  totalBytes: number;
  /** 남은 용량으로 부족한 바이트. 0 이면 여유 있음. */
  quotaShortfall: number;
}

/**
 * 바이트 하나 보내기 전에 전 항목을 검증한다.
 *
 * 규칙은 서버 `normalize_relpath` 와 일치한다(`fileTree.ts` 주석 참조). 여기서 걸러도
 * 서버 검증은 그대로 남는다 — 사전 검사는 UX 이지 강제가 아니다.
 *
 * 쿼터도 마찬가지다. 검사와 실제 업로드 사이에 다른 세션이 용량을 쓸 수 있고 최종 판정은
 * 서버의 조건부 UPDATE 가 한다. 통과해도 413 이 올 수 있다.
 */
export function preflight(tree: CollectedTree, remainingBytes: number): PreflightResult {
  const entries: CollectedFile[] = [];
  const skipped: SkippedEntry[] = [];
  let totalBytes = 0;

  for (const item of tree.files) {
    const norm = normalizeRelPath(item.path);
    if ("error" in norm) {
      skipped.push({ path: item.path, reason: norm.error });
      continue;
    }
    if (item.file.size > MAX_FILE_SIZE) {
      skipped.push({ path: item.path, reason: `${formatBytes(item.file.size)} (파일 1개 최대 10GB)` });
      continue;
    }
    entries.push({ path: norm.segments.join("/"), file: item.file });
    totalBytes += item.file.size;
  }

  const dirs: string[] = [];
  for (const raw of tree.dirs) {
    const norm = normalizeRelPath(raw);
    // 디렉터리 경로가 규칙을 어기면 그 안의 파일도 이미 위에서 걸러졌다. 조용히 건너뛴다.
    if (!("error" in norm)) dirs.push(norm.segments.join("/"));
  }

  return {
    entries,
    dirs: [...new Set(dirs)],
    skipped,
    totalBytes,
    quotaShortfall: Math.max(0, totalBytes - remainingBytes),
  };
}

export interface FolderUploadCallbacks {
  /** 전송된 누적 바이트. 진행률 표시용. */
  onProgress: (loadedBytes: number) => void;
  /** 완료된 파일 수 / 전체. */
  onCount: (done: number, total: number) => void;
}

export interface FolderUploadResult {
  succeeded: number;
  failed: SkippedEntry[];
}

/** 경로에서 디렉터리 부분만 잘라낸다. 최상위 파일이면 빈 문자열. */
function dirOf(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx < 0 ? "" : path.slice(0, idx);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * 429 를 사용자에게 노출하지 않고 Retry-After 만큼 기다렸다 재시도한다.
 *
 * 배치가 rate limit 을 흡수하는 게 이 설계의 목적이므로, 사용자가 429 를 본다면 그건
 * 사용자가 고칠 수 있는 문제가 아니다. rate limit 은 핸들러 진입 전에 걸리므로 재시도가
 * 항목을 중복 생성하지 않는다.
 */
async function withRateLimitRetry<T>(run: () => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await run();
    } catch (err) {
      if (errorStatus(err) !== 429 || attempt >= RATE_LIMIT_RETRIES) throw err;
      await sleep((retryAfterSeconds(err) ?? 2) * 1000);
    }
  }
}

/** 태스크를 동시 limit 개까지만 돌린다. */
async function runPool(tasks: (() => Promise<void>)[], limit: number): Promise<void> {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= tasks.length) return;
      await tasks[index]();
    }
  });
  await Promise.all(workers);
}

/** 항목 하나가 multipart 본문에서 차지하는 크기 추정치(내용 + 오버헤드). */
function wireSize(entry: CollectedFile): number {
  // 경로/파일명은 UTF-8 로 인코딩되어 헤더에 두 번(파일 파트 filename, paths 파트 값) 들어간다.
  const nameBytes = new TextEncoder().encode(entry.path).length * 2;
  return entry.file.size + nameBytes + MULTIPART_ENTRY_OVERHEAD;
}

/**
 * 배치 대상 파일을 개수/크기 상한 안에서 greedy 하게 묶는다.
 *
 * 파일 내용만이 아니라 **전송될 본문 크기**를 기준으로 센다 — 서버가 재는 것이 그것이기
 * 때문이다. 내용 합계로만 세면 오버헤드만큼 초과해 배치 전체가 413 으로 날아간다.
 */
function groupBatches(entries: CollectedFile[]): CollectedFile[][] {
  const batches: CollectedFile[][] = [];
  let current: CollectedFile[] = [];
  let bytes = 0;

  for (const entry of entries) {
    const size = wireSize(entry);
    const wouldExceed =
      current.length >= BATCH_MAX_FILES || bytes + size > BATCH_MAX_CONTENT_BYTES;
    if (current.length > 0 && wouldExceed) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(entry);
    bytes += size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

export async function runFolderUpload(
  plan: PreflightResult,
  parentId: number | null,
  cb: FolderUploadCallbacks,
): Promise<FolderUploadResult> {
  const failed: SkippedEntry[] = [];
  let succeeded = 0;
  let done = 0;
  const total = plan.entries.length;

  // 전송 단위별 누적 바이트 — 재시도로 되감길 수 있어 합산 시점에 다시 더한다.
  const loaded = new Map<string, number>();
  const reportProgress = () => {
    let sum = 0;
    for (const v of loaded.values()) sum += v;
    cb.onProgress(sum);
  };
  const finishOne = () => {
    done += 1;
    cb.onCount(done, total);
  };

  // ── 1단계: 폴더 트리 확정 ────────────────────────────────
  // 파일 0개 요청으로 전 디렉터리를 만들고 경로 → 폴더 id 맵을 받는다. 배치에 파일이 하나도
  // 속하지 않는 폴더(예: 1.5GB 파일 하나만 든 폴더)도 여기서 id 를 얻는다.
  let folders: Record<string, number> = {};
  if (plan.dirs.length > 0) {
    const res = await withRateLimitRetry(() => batchUpload([], plan.dirs, parentId));
    folders = res.folders;
  }

  const parentOf = (path: string): number | null => {
    const dir = dirOf(path);
    if (!dir) return parentId;
    return folders[dir] ?? parentId;
  };

  // ── 2단계: 파일 전송 ─────────────────────────────────────
  const small = plan.entries.filter((e) => e.file.size <= BATCH_MAX_BYTES);
  const large = plan.entries.filter((e) => e.file.size > BATCH_MAX_BYTES);

  /**
   * 배치 하나를 보낸다. 413(본문 상한 초과)이면 **절반으로 쪼개 다시 보낸다.**
   *
   * groupBatches 의 오버헤드 추정이 빗나가도 스스로 회복하게 하는 안전망이다. 추정만
   * 믿으면 경로가 유난히 긴 트리에서 배치가 통째로 날아간다 — 실제로 그 형태로 한 번에
   * 160건이 실패한 적이 있다. 쪼갤 수 없는 1개짜리까지 갔는데도 413 이면 그때 실패 처리한다.
   */
  const sendBatch = async (batch: CollectedFile[], key: string): Promise<void> => {
    try {
      const res = await withRateLimitRetry(() => {
        loaded.set(key, 0); // 재시도 시 이 배치 몫을 되감는다.
        reportProgress();
        return batchUpload(batch, [], parentId, (bytes) => {
          loaded.set(key, bytes);
          reportProgress();
        });
      });
      for (const item of res.items) {
        if (item.status === "created") succeeded += 1;
        else
          failed.push({
            path: item.path,
            reason: uploadErrorMessage(item.code ?? undefined, item.detail ?? undefined),
          });
        finishOne();
      }
      // 전송 완료분을 실제 크기로 확정한다(진행률이 100% 못 미치고 멈추는 것 방지).
      loaded.set(key, batch.reduce((sum, e) => sum + e.file.size, 0));
      reportProgress();
    } catch (err) {
      if (errorStatus(err) === 413 && batch.length > 1) {
        loaded.set(key, 0);
        const mid = Math.ceil(batch.length / 2);
        await sendBatch(batch.slice(0, mid), `${key}a`);
        await sendBatch(batch.slice(mid), `${key}b`);
        return;
      }
      const reason = uploadErrorFromException(err);
      for (const entry of batch) {
        failed.push({ path: entry.path, reason });
        finishOne();
      }
    }
  };

  const batchTasks = groupBatches(small).map(
    (batch, index) => () => sendBatch(batch, `batch:${index}`),
  );

  const largeTasks = large.map((entry) => async () => {
    const key = `file:${entry.path}`;
    const target = parentOf(entry.path);
    const track = (percent: number) => {
      loaded.set(key, Math.round((percent / 100) * entry.file.size));
      reportProgress();
    };
    // 단일/재개 경로는 파일명을 file.name 에서 가져오므로, 정규화로 이름이 달라졌을 때
    // 배치 경로와 결과가 갈리지 않도록 경로의 마지막 세그먼트로 맞춘 사본을 쓴다.
    const named = withNormalizedName(entry);
    try {
      if (entry.file.size > RESUMABLE_THRESHOLD) {
        await runResumable(named, target, track);
      } else {
        await withRateLimitRetry(() => uploadFile(named, target, track));
      }
      loaded.set(key, entry.file.size);
      reportProgress();
      succeeded += 1;
    } catch (err) {
      failed.push({ path: entry.path, reason: uploadErrorFromException(err) });
    }
    finishOne();
  });

  await runPool([...batchTasks, ...largeTasks], BATCH_CONCURRENCY);
  return { succeeded, failed };
}

/**
 * 파일명을 정규화된 경로의 마지막 세그먼트로 맞춘 사본. 이름이 이미 같으면 원본을 그대로 쓴다.
 * File 생성자는 Blob 을 참조만 하므로 내용을 복사하지 않는다.
 */
function withNormalizedName(entry: CollectedFile): File {
  const wanted = entry.path.split("/").pop() ?? entry.file.name;
  if (wanted === entry.file.name) return entry.file;
  return new File([entry.file], wanted, { type: entry.file.type });
}

/** 재개 가능 업로드를 완료까지 기다리는 프로미스로 감싼다. */
function runResumable(
  named: File,
  parentId: number | null,
  onPercent: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    void startNewResumableUpload(named, parentId, {
      onProgress: onPercent,
      onStatus: (status) => {
        if (status === "canceled") reject(new Error("업로드가 취소되었습니다"));
      },
      onDone: () => resolve(),
      onError: (message) => reject(new Error(message)),
    })
      .then((controller) => controller.start())
      .catch(reject);
  });
}
