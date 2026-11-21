/**
 * 인증 SSE 구독 공용부 — 파일 이벤트(lib/fileEvents.ts)와 BeeMate(pages/BeematePage.tsx)가 쓴다.
 *
 * 백엔드 SSE 는 인증이 Authorization 헤더로 걸려 있어 브라우저 EventSource(헤더 지정 불가)를
 * 쓸 수 없다. 그래서 fetch 스트리밍으로 직접 프레임을 파싱한다. `data:` JSON 라인만 콜백으로
 * 넘기고, 주석 라인 중 `: connected` 는 onOpen 으로, `: ping` 은 무시한다.
 *
 * onOpen 을 응답 헤더가 아니라 `: connected` 프레임에 거는 이유 — 서버는 구독을 마친 **뒤에**
 * 이 주석을 보낸다. 헤더 시점에 밀린 목록을 조회하면 구독 직전에 온 이벤트를 놓칠 수 있다.
 *
 * 연결이 끊기면(네트워크 오류, 스트림 종료, 서버 재시작) 지수 백오프로 재연결하고, 재연결에
 * 성공하면 onReconnect 를 한 번 호출한다(끊긴 동안 놓친 변경 보정). 매 연결마다 tokenStore 에서
 * 최신 access token 을 읽으므로 axios 인터셉터가 회전시킨 새 토큰을 자연히 이어받는다(독립
 * refresh 를 하지 않아 회전 경합을 피한다).
 */

import { withBase } from "@/lib/basePath";
import { getAccessToken } from "@/lib/tokenStore";

export interface SseHandlers<T> {
  /** `data:` 프레임 하나를 파싱한 이벤트. */
  onEvent: (event: T) => void;
  /** 서버가 구독을 마쳤다(`: connected`). 첫 연결과 재연결 모두에서 불린다. */
  onOpen?: () => void;
  /** 재연결 성공 시 1회 — 끊긴 동안의 변경을 보정하도록 전체 재조회에 쓴다. */
  onReconnect?: () => void;
  /** 403·404·409·422 처럼 다시 시도해도 소용없는 응답. 주면 재연결하지 않고 이것을 부른다. */
  onFatal?: (status: number, detail: string) => void;
}

// 지수 백오프 지연(ms): 1s → 2s → … → 최대 30s.
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

class FatalStatus extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`SSE 응답 오류 ${status}`);
  }
}

/** `path`(선행 "/" 절대경로, 베이스는 붙인다)를 구독한다. 반환 함수로 해제한다. */
export function subscribeSse<T>(path: string, handlers: SseHandlers<T>): () => void {
  let closed = false;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  // 첫 연결이 아니라 재연결로 열렸는지 — 열림 확인 시 onReconnect 를 한 번 쏘기 위한 플래그.
  let reconnecting = false;

  const dispatchFrame = (frame: string) => {
    for (const rawLine of frame.split("\n")) {
      const line = rawLine.trimEnd();
      if (line === ": connected") {
        handlers.onOpen?.();
        continue;
      }
      if (!line.startsWith("data:")) continue;
      const json = line.slice("data:".length).trim();
      if (!json) continue;
      try {
        handlers.onEvent(JSON.parse(json) as T);
      } catch {
        /* 손상된 프레임은 건너뛴다 */
      }
    }
  };

  const scheduleReconnect = () => {
    if (closed) return;
    reconnecting = true;
    const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    retryTimer = setTimeout(() => {
      void connect();
    }, delay);
  };

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    const token = getAccessToken();
    try {
      const res = await fetch(withBase(path), {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
      });
      if ([403, 404, 409, 422].includes(res.status) && handlers.onFatal) {
        let detail = "";
        try {
          detail = ((await res.json()) as { detail?: string }).detail ?? "";
        } catch {
          /* 본문 없음 */
        }
        throw new FatalStatus(res.status, detail);
      }
      if (!res.ok || !res.body) throw new Error(`SSE 응답 오류 ${res.status}`);

      // 스트림이 열렸다 — 백오프 리셋. 재연결이면 전체 재조회를 트리거한다.
      attempt = 0;
      if (reconnecting) {
        reconnecting = false;
        handlers.onReconnect?.();
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE 프레임 경계는 빈 줄(\n\n). 완성된 프레임만 떼어 처리한다.
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          dispatchFrame(buffer.slice(0, sep));
          buffer = buffer.slice(sep + 2);
        }
      }
      // 스트림이 정상 종료됨(서버 재시작 등) — 재연결.
      throw new Error("SSE 스트림 종료");
    } catch (err) {
      // 명시적 해제/취소면 조용히 끝낸다. 그 외 오류는 백오프 재연결.
      if (closed || controller?.signal.aborted) return;
      if (err instanceof FatalStatus) {
        closed = true;
        handlers.onFatal?.(err.status, err.detail);
        return;
      }
      scheduleReconnect();
    }
  };

  void connect();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    controller?.abort();
  };
}
