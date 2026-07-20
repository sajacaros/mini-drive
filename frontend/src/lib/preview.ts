/**
 * 파일/공유 미리보기 로더 (PRD 3.2, 3.4).
 *
 * 백엔드는 두 경로가 같은 응답 형태를 공유한다:
 *   - 인증 파일:  GET  /api/files/{id}/preview        (apiClient — 인증 헤더 자동 첨부)
 *   - 공개 공유:  POST /api/public/shares/{url}/preview (무인증 — 순수 fetch, 비밀번호 옵션)
 *
 * 응답 규약:
 *   - 이미지/PDF: 게이트웨이 인라인 스트리밍(바이트) → blob → objectURL 로 <img>/<iframe> 렌더.
 *   - 텍스트:     text/plain 본문. X-Preview-Truncated: true 면 앞부분만 표시된 것.
 *   - 미지원:     415 → "미리보기 미지원, 다운로드" 폴백.
 *
 * objectURL 은 호출부가 화면에서 내릴 때 releasePreview() 로 해제해야 누수가 없다.
 */

import { errorStatus } from "@/api/client";
import apiClient from "@/api/client";
import { withBase } from "@/lib/basePath";

export type PreviewResult =
  | { kind: "image"; url: string }
  | { kind: "pdf"; url: string }
  | { kind: "text"; text: string; truncated: boolean }
  | { kind: "unsupported" };

/** objectURL 을 만든 미리보기 결과라면 해제한다(text/unsupported 는 no-op). */
export function releasePreview(result: PreviewResult | null): void {
  if (result && (result.kind === "image" || result.kind === "pdf")) {
    URL.revokeObjectURL(result.url);
  }
}

/** content-type + blob 을 PreviewResult 로 변환한다(이미지/PDF/텍스트 공통). */
async function toResult(
  contentType: string,
  blob: Blob,
  truncated: boolean,
): Promise<PreviewResult> {
  const ct = contentType.toLowerCase();
  if (ct.startsWith("text/")) {
    return { kind: "text", text: await blob.text(), truncated };
  }
  if (ct.includes("application/pdf")) {
    return { kind: "pdf", url: URL.createObjectURL(blob) };
  }
  // 나머지는 이미지로 간주(백엔드가 image/*·pdf·text 외에는 415 로 거른다).
  return { kind: "image", url: URL.createObjectURL(blob) };
}

/** 인증 사용자의 파일 미리보기. 415 는 unsupported 로 흡수, 그 외 오류는 던진다. */
export async function fetchFilePreview(fileId: number): Promise<PreviewResult> {
  try {
    const res = await apiClient.get<Blob>(`/files/${fileId}/preview`, {
      responseType: "blob",
    });
    const contentType = String(res.headers["content-type"] ?? "");
    const truncated = String(res.headers["x-preview-truncated"] ?? "") === "true";
    return await toResult(contentType, res.data, truncated);
  } catch (err) {
    if (errorStatus(err) === 415) return { kind: "unsupported" };
    throw err;
  }
}

/** 공개 공유 미리보기 오류(비밀번호/만료 등)를 status 와 함께 던진다. */
export class SharePreviewError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * 공개 공유 미리보기 (무인증). 비밀번호가 있으면 body 에 담는다. 다운로드 횟수는 소모하지 않는다.
 * 415 → unsupported, 401(비밀번호)/410(만료)/400(폴더) 등은 SharePreviewError 로 던진다.
 */
export async function fetchPublicSharePreview(
  shareUrl: string,
  password?: string,
): Promise<PreviewResult> {
  const res = await fetch(withBase(`/api/public/shares/${shareUrl}/preview`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: password ?? null }),
  });

  if (res.status === 415) return { kind: "unsupported" };
  if (!res.ok) {
    let detail = "미리보기를 불러오지 못했습니다.";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* JSON 이 아니면 기본 메시지 유지 */
    }
    throw new SharePreviewError(res.status, detail);
  }

  const contentType = res.headers.get("content-type") ?? "";
  const truncated = res.headers.get("x-preview-truncated") === "true";
  return await toResult(contentType, await res.blob(), truncated);
}
