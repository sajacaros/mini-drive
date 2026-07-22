/**
 * 파일 다운로드 헬퍼 (티켓 방식 — Phase 2).
 *
 * 다운로드는 무헤더 GET 으로 스트리밍하도록 1회용 티켓 기반으로 전환됐다(PRD 2.2). 흐름:
 *   1. axios(인증 헤더 포함) 또는 fetch 로 `download-ticket` 을 발급받는다.
 *   2. 반환된 무헤더 URL 을 브라우저 네비게이션(숨김 a[href])으로 열어 nginx→MinIO 스트리밍을
 *      받는다. 응답 본문을 메모리에 적재하지 않으므로 대용량 파일에도 안전하다.
 *
 * 파일명은 게이트웨이 응답의 Content-Disposition 이 결정하므로 프론트에서 지정하지 않는다.
 */

import {
  issueArchiveDownloadTicket,
  issueDownloadTicket,
  issueVersionDownloadTicket,
} from "@/api/files";
import type { DownloadTicketResponse } from "@/api/types";
import { withBase } from "@/lib/basePath";

/**
 * 티켓 URL 로 브라우저 네이티브 다운로드를 트리거한다 (숨김 a[href] 네비게이션).
 * 백엔드가 돌려준 url 은 "/api/files/download?ticket=..." 같은 절대경로이므로, 서브패스
 * 배포에서 게이트웨이에 닿도록 런타임 베이스를 앞에 붙인다(base "/" 면 그대로).
 */
function navigateToDownload(url: string): void {
  const a = document.createElement("a");
  a.href = url.startsWith("/") ? withBase(url) : url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** 인증 사용자의 현재 버전 다운로드 (티켓 발급 → 무헤더 스트리밍). */
export async function downloadFile(fileId: number): Promise<void> {
  const ticket = await issueDownloadTicket(fileId);
  navigateToDownload(ticket.url);
}

/**
 * 폴더 또는 여러 항목을 ZIP 하나로 다운로드한다.
 * 단일 다운로드와 같은 티켓 흐름이고, 스트리밍만 backend 가 맡는다(무압축 ZIP).
 * 개수·용량 상한 초과는 티켓 발급 단계에서 413 으로 걸러져 호출부가 안내할 수 있다.
 */
export async function downloadArchive(fileIds: number[]): Promise<void> {
  const ticket = await issueArchiveDownloadTicket(fileIds);
  navigateToDownload(ticket.url);
}

/** 항목 하나 다운로드 — 파일은 원본 그대로, 폴더는 하위 전체를 ZIP 으로. 호출부의 분기를 없앤다. */
export async function downloadNode(file: { id: number; is_folder: boolean }): Promise<void> {
  if (file.is_folder) await downloadArchive([file.id]);
  else await downloadFile(file.id);
}

/** 인증 사용자의 특정 버전 다운로드. */
export async function downloadFileVersion(fileId: number, version: number): Promise<void> {
  const ticket = await issueVersionDownloadTicket(fileId, version);
  navigateToDownload(ticket.url);
}

/**
 * 공개 공유 다운로드 (POST /api/public/shares/{shareUrl}/download-ticket).
 * 무인증 경로이므로 apiClient 대신 순수 fetch 로 티켓을 발급받는다. 비밀번호가 있으면 body 에
 * 담는다. 401(비밀번호 필요/오류)/410(만료·비활성·횟수 초과) 등은 status 를 담은 에러로 던져
 * 호출부가 상태별 안내를 하게 한다.
 */
export class ShareDownloadError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function downloadSharedFile(shareUrl: string, password?: string): Promise<void> {
  const res = await fetch(withBase(`/api/public/shares/${shareUrl}/download-ticket`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: password ?? null }),
  });

  if (!res.ok) {
    let detail = "다운로드에 실패했습니다.";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* JSON 이 아니면 기본 메시지 유지 */
    }
    throw new ShareDownloadError(res.status, detail);
  }

  const ticket = (await res.json()) as DownloadTicketResponse;
  navigateToDownload(ticket.url);
}
