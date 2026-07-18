/**
 * 파일 다운로드 헬퍼.
 *
 * 백엔드 다운로드 응답은 X-Accel-Redirect(게이트웨이 스트리밍, PRD 2.2)이고, 다운로드
 * 엔드포인트는 Authorization(Bearer) 인증이 필수다. 쿼리 파라미터 토큰을 지원하지 않으므로
 * 단순한 `window.location`/`a[href]` 네비게이션으로는 토큰을 실을 수 없다. 따라서 fetch 로
 * Authorization 헤더를 붙여 받은 뒤 blob 으로 저장한다.
 *
 * 한계: fetch→blob 방식은 응답 본문을 메모리에 모두 적재하므로 초대형 파일(수 GB)에는
 * 부적합하다. 근본 해결은 백엔드가 단기 서명 쿼리 토큰(예: ?token=...) 다운로드를 지원해
 * 브라우저 네비게이션 스트리밍을 허용하는 것 — Phase 후속 개선 제안으로 남긴다.
 */

import { getAccessToken } from "./tokenStore";

/** Content-Disposition 헤더에서 파일명을 파싱한다 (filename* 우선). */
function parseFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* fallthrough */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1] ?? fallback;
}

/** blob 을 받아 브라우저 다운로드를 트리거한다. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 즉시 revoke 하면 일부 브라우저에서 다운로드가 취소되므로 살짝 지연한다.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 인증 사용자의 파일 다운로드 (GET /api/files/{id}/download). */
export async function downloadFile(fileId: number, fallbackName: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`/api/files/${fileId}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    throw new Error(`다운로드 실패 (${res.status})`);
  }
  const blob = await res.blob();
  const filename = parseFilename(res.headers.get("Content-Disposition"), fallbackName);
  saveBlob(blob, filename);
}

/**
 * 공개 공유 다운로드 (POST /api/public/shares/{shareUrl}/download).
 * 404/410/401 등은 status 를 담은 에러로 던져 호출부가 상태별 안내를 하게 한다.
 */
export class ShareDownloadError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function downloadSharedFile(
  shareUrl: string,
  fallbackName: string,
  password?: string,
): Promise<void> {
  const res = await fetch(`/api/public/shares/${shareUrl}/download`, {
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

  const blob = await res.blob();
  const filename = parseFilename(res.headers.get("Content-Disposition"), fallbackName);
  saveBlob(blob, filename);
}
