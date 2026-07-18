/**
 * 재개 가능 업로드 오케스트레이션 (PRD 3.2).
 *
 * 임계값(RESUMABLE_THRESHOLD) 초과 파일은 이 경로로 분기한다. 흐름:
 *   1. 세션 개시 → 응답의 part_size 로 청크를 나눈다(마지막 파트 제외 정확히 part_size).
 *   2. 각 청크를 PUT .../parts/{n} 에 raw 바이트로 순차 전송. 진행률/일시정지/재개/취소 지원.
 *   3. 완료 시 POST .../complete → FileResponse.
 *
 * 재개(새로고침 포함): session_id 등 메타를 localStorage 에 보관한다. 다만 브라우저 보안상 File
 * 바이트는 새로고침 후 복구할 수 없으므로, 재개하려면 사용자가 같은 파일을 다시 선택해야 한다.
 * 재선택 시 GET .../uploads/{id} 의 uploaded_parts 를 보고 빠진 파트만 이어올린다. 세션 404 면
 * 만료된 것이므로 처음부터 다시 시작한다.
 */

import type { AxiosError } from "axios";

import { errorStatus } from "@/api/client";
import {
  abortResumableUpload,
  completeResumableUpload,
  getResumableSession,
  initResumableReupload,
  initResumableUpload,
  uploadResumablePart,
} from "@/api/files";
import type { FileNode, ResumableSession } from "@/api/types";

/** 이 크기(바이트) 초과 파일은 재개 가능 업로드로 분기한다. 1 GiB. */
export const RESUMABLE_THRESHOLD = 1024 * 1024 * 1024;

export type UploadStatus = "uploading" | "paused" | "completed" | "error" | "canceled";

/** 새로고침 후 재개를 위해 localStorage 에 보관하는 세션 메타. */
export interface PendingSession {
  sessionId: string;
  name: string;
  size: number;
  kind: "new" | "version";
  /** version 재개 시 대상 파일 id (표시/재선택 매칭용). new 는 백엔드가 만든 임시 파일 id. */
  fileId: number;
  parentId: number | null;
  expiresAt: string;
}

const STORE_KEY = "minidrive:resumable";

function readStore(): PendingSession[] {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? (arr as PendingSession[]) : [];
  } catch {
    return [];
  }
}

function writeStore(sessions: PendingSession[]): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(sessions));
  } catch {
    /* 저장 실패는 무시 — 재개 편의 기능일 뿐 업로드 자체는 진행된다. */
  }
}

function rememberSession(s: PendingSession): void {
  const rest = readStore().filter((x) => x.sessionId !== s.sessionId);
  writeStore([...rest, s]);
}

export function forgetSession(sessionId: string): void {
  writeStore(readStore().filter((x) => x.sessionId !== sessionId));
}

/** 만료 시각이 지나지 않은 보관 세션만 (표시용). */
export function listPendingSessions(): PendingSession[] {
  const now = Date.now();
  const alive = readStore().filter((s) => new Date(s.expiresAt).getTime() > now);
  if (alive.length !== readStore().length) writeStore(alive);
  return alive;
}

interface ControllerCallbacks {
  onProgress: (percent: number) => void;
  onStatus: (status: UploadStatus) => void;
  onDone: (file: FileNode) => void;
  onError: (message: string) => void;
}

/**
 * 단일 재개 업로드를 제어한다. 파트를 순차 전송하며 일시정지/재개/취소를 지원한다.
 * pause 는 진행 중 요청을 중단(abort)한 뒤 멈추고, resume 은 남은 파트부터 다시 올린다.
 */
export class ResumableController {
  private session: ResumableSession;
  private file: File;
  private cb: ControllerCallbacks;

  private uploaded: Set<number>;
  private uploadedBytes: number;
  private paused = false;
  private canceled = false;
  private running = false;
  private abort: AbortController | null = null;

  constructor(session: ResumableSession, file: File, cb: ControllerCallbacks) {
    this.session = session;
    this.file = file;
    this.cb = cb;
    this.uploaded = new Set(session.uploaded_parts);
    this.uploadedBytes = session.received_bytes;
  }

  get sessionId(): string {
    return this.session.session_id;
  }

  private partBounds(n: number): [number, number] {
    const start = (n - 1) * this.session.part_size;
    const end = Math.min(start + this.session.part_size, this.session.total_size);
    return [start, end];
  }

  private reportProgress(currentLoaded: number): void {
    const total = this.session.total_size;
    const pct = total > 0 ? Math.min(100, Math.round(((this.uploadedBytes + currentLoaded) / total) * 100)) : 0;
    this.cb.onProgress(pct);
  }

  /** 업로드 루프 시작(또는 pause 이후 재개). */
  async start(): Promise<void> {
    if (this.running || this.canceled) return;
    this.running = true;
    this.paused = false;
    this.cb.onStatus("uploading");
    this.reportProgress(0);

    try {
      for (let n = 1; n <= this.session.total_parts; n++) {
        if (this.paused || this.canceled) break;
        if (this.uploaded.has(n)) continue;

        const [start, end] = this.partBounds(n);
        const chunk = this.file.slice(start, end);
        this.abort = new AbortController();

        try {
          const res = await uploadResumablePart(this.session.session_id, n, chunk, {
            signal: this.abort.signal,
            onProgress: (loaded) => this.reportProgress(loaded),
          });
          this.uploaded.add(res.part_number);
          this.uploadedBytes += res.size;
          this.reportProgress(0);
        } catch (err) {
          // pause/cancel 로 인한 abort 는 오류가 아니다 — 루프만 빠져나간다.
          if (this.paused || this.canceled) break;
          throw err;
        } finally {
          this.abort = null;
        }
      }

      if (this.canceled) return;
      if (this.paused) {
        this.running = false;
        this.cb.onStatus("paused");
        return;
      }

      // 모든 파트 완료 → 병합/확정.
      const file = await completeResumableUpload(this.session.session_id);
      forgetSession(this.session.session_id);
      this.running = false;
      this.cb.onStatus("completed");
      this.cb.onProgress(100);
      this.cb.onDone(file);
    } catch (err) {
      this.running = false;
      if (this.canceled) return;
      // 세션이 사라졌으면(404/410) 만료된 것 — 보관 메타를 정리한다.
      const status = errorStatus(err);
      if (status === 404 || status === 410) forgetSession(this.session.session_id);
      this.cb.onStatus("error");
      this.cb.onError(resumableErrorMessage(err));
    }
  }

  pause(): void {
    if (!this.running) return;
    this.paused = true;
    this.abort?.abort();
    this.running = false;
    this.cb.onStatus("paused");
  }

  resume(): void {
    if (this.running || this.canceled) return;
    void this.start();
  }

  /** 취소 — 진행 중 요청 중단 후 세션을 서버에서 폐기하고 보관 메타를 지운다. */
  async cancel(): Promise<void> {
    this.canceled = true;
    this.abort?.abort();
    this.running = false;
    forgetSession(this.session.session_id);
    this.cb.onStatus("canceled");
    try {
      await abortResumableUpload(this.session.session_id);
    } catch {
      /* 이미 만료/삭제됐을 수 있음 — 무시 */
    }
  }
}

/** 새 파일 재개 업로드 시작 — 세션 개시 후 컨트롤러 반환. */
export async function startNewResumableUpload(
  file: File,
  parentId: number | null,
  cb: ControllerCallbacks,
): Promise<ResumableController> {
  const session = await initResumableUpload({
    filename: file.name,
    parent_id: parentId,
    total_size: file.size,
    mime_type: file.type || undefined,
  });
  rememberSession(toPending(session, file, parentId));
  return new ResumableController(session, file, cb);
}

/** 새 버전 재개 업로드 시작. baseVersion 생략 시 강제 덮어쓰기. */
export async function startVersionResumableUpload(
  fileId: number,
  file: File,
  baseVersion: number | undefined,
  parentId: number | null,
  cb: ControllerCallbacks,
): Promise<ResumableController> {
  const session = await initResumableReupload(fileId, {
    total_size: file.size,
    mime_type: file.type || undefined,
    base_version: baseVersion ?? null,
  });
  rememberSession(toPending(session, file, parentId));
  return new ResumableController(session, file, cb);
}

/**
 * 보관된 세션을 재선택된 파일로 재개한다. 파일 크기가 다르면(다른 파일) 거절한다.
 * 세션이 만료(404)됐으면 보관 메타를 지우고 null 을 반환해 호출부가 처음부터 다시 하도록 한다.
 */
export async function resumeFromPending(
  pending: PendingSession,
  file: File,
  cb: ControllerCallbacks,
): Promise<ResumableController | null> {
  if (file.size !== pending.size) {
    throw new Error("선택한 파일이 중단된 업로드와 크기가 다릅니다.");
  }
  let session: ResumableSession;
  try {
    session = await getResumableSession(pending.sessionId);
  } catch (err) {
    if (errorStatus(err) === 404) {
      forgetSession(pending.sessionId);
      return null;
    }
    throw err;
  }
  return new ResumableController(session, file, cb);
}

function toPending(session: ResumableSession, file: File, parentId: number | null): PendingSession {
  return {
    sessionId: session.session_id,
    name: file.name,
    size: file.size,
    kind: session.kind,
    fileId: session.file_id,
    parentId,
    expiresAt: session.expires_at,
  };
}

function resumableErrorMessage(err: unknown): string {
  const status = errorStatus(err);
  if (status === 413) return "저장 용량 초과";
  if (status === 409) return "버전 충돌";
  if (status === 422) return "청크 크기가 올바르지 않습니다";
  if (status === 404 || status === 410) return "업로드 세션이 만료되었습니다";
  const ax = err as AxiosError<{ detail?: string }>;
  const detail = ax?.response?.data?.detail;
  return typeof detail === "string" ? detail : "업로드 실패";
}
