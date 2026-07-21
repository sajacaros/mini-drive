/**
 * 업로드 실패 사유 → 사용자 문구.
 *
 * 단일 업로드·재개 가능 업로드·배치 업로드 세 경로가 공유한다. 같은 원인에 경로마다 다른
 * 문구가 나오지 않게 하려고 한곳에 모았다.
 */

import { errorStatus, extractErrorMessage } from "@/api/client";

/**
 * HTTP 상태 코드를 문구로 옮긴다.
 *
 * 413 은 원인이 둘(파일 크기 상한 / 저장 용량 할당량)이라 상태 코드만으로는 구분되지
 * 않는다. 서버가 보낸 detail 이 있으면 그쪽이 더 정확하므로 우선한다.
 */
export function uploadErrorMessage(code: number | undefined, detail?: string): string {
  if (detail) return detail;
  switch (code) {
    case 409:
      return "같은 이름의 항목이 이미 있습니다";
    case 413:
      return "저장 용량을 초과했습니다";
    case 403:
    case 404:
      return "이 폴더에 업로드할 권한이 없습니다";
    case 422:
      return "경로나 이름에 쓸 수 없는 문자가 있습니다";
    case 502:
      return "저장소 오류로 저장하지 못했습니다";
    default:
      return "업로드에 실패했습니다";
  }
}

/** axios 예외를 문구로 옮긴다(단일/재개 업로드 경로용). */
export function uploadErrorFromException(err: unknown): string {
  const status = errorStatus(err);
  const detail = extractErrorMessage(err, "");
  return uploadErrorMessage(status, detail || undefined);
}
