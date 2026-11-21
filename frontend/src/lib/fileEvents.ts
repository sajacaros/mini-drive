/**
 * 파일 변경 실시간 이벤트 구독 (Phase 8-1, spec/drive-ux-phase8.md).
 *
 * 백엔드 `GET /api/files/events` SSE 스트림. 연결·파싱·백오프 재연결은 공용부(lib/sse.ts)가
 * 맡고, 여기는 이벤트 모양과 구독 경로만 정한다.
 */

import { subscribeSse } from "@/lib/sse";

/**
 * SSE 페이로드 (backend file_events 의 발행 함수들과 1:1).
 *
 * `type="purge"`(영구 삭제, spec/trash-retention-purge.md)만 모양이 조금 다르다. 자동 정리는
 * 행위자가 없어 `actor_id` 가 null 이고, 회차당 발행 상한을 넘긴 몫은 소유자별 요약 1건으로
 * 접히면서 `file_id`·`name` 이 빠지고 `purged`(건수)가 붙는다. 그래서 세 필드가 옵셔널이다.
 */
export interface FileEvent {
  type: string;
  /** 요약 이벤트에는 없다 — 없으면 개별 행 제거가 아니라 목록 재조회로 처리한다. */
  file_id?: number | null;
  parent_folder_id: number | null;
  /** 자동 정리처럼 행위자가 없는 이벤트는 null. */
  actor_id: number | null;
  name?: string | null;
  /** purge 전용 — 행이 이미 없어 서버가 소유자 판정에 쓰는 값(구독자 필터 참조). */
  owner_id?: number;
  /** purge 요약 전용 — 접힌 건수. */
  purged?: number;
  ts: string;
}

export interface FileEventsHandlers {
  /** `data:` 프레임 하나를 파싱한 이벤트. */
  onEvent: (event: FileEvent) => void;
  /** 재연결 성공 시 1회 호출 — 끊긴 동안의 변경을 보정하도록 전체 재조회에 쓴다. */
  onReconnect?: () => void;
}

/**
 * 파일 이벤트 스트림을 구독한다. 반환한 함수를 호출하면 구독을 해제한다(진행 중 fetch 취소 +
 * 예약된 재연결 취소).
 */
export function subscribeFileEvents(handlers: FileEventsHandlers): () => void {
  return subscribeSse<FileEvent>("/api/files/events", handlers);
}
