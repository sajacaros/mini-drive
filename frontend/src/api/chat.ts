/**
 * 챗봇 API (PRD 6.9, Phase 7-2). 세션 CRUD 는 apiClient(axios)로, 질문 전송은 SSE 스트림이라
 * EventSource(=GET 전용)를 쓸 수 없어 fetch + ReadableStream 으로 직접 파싱한다.
 */

import apiClient from "./client";
import { getAccessToken } from "@/lib/tokenStore";

/** 인용(출처) — 백엔드가 검색 통과 청크에서 구조적으로 산출한다. file_name 은 없을 수 있다. */
export interface ChatCitation {
  file_id: number;
  file_name?: string | null;
  chunk_id: number;
  version: number;
  snippet: string;
}

export interface ChatSessionSummary {
  id: number;
  title: string;
  /** 지정 시 검색이 그 위키 스페이스 범위로 제한된다. null = 전체 접근 범위. */
  space_id: number | null;
  created_at: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: ChatCitation[] | null;
  created_at: string;
}

export interface ChatSessionDetail {
  id: number;
  title: string;
  space_id: number | null;
  created_at: string;
  messages: ChatMessage[];
}

/** 세션 생성. spaceId 를 주면 검색이 그 위키 스페이스 범위로 제한된다(생략 = 전체 범위). */
export async function createSession(
  title?: string,
  spaceId?: number | null,
): Promise<ChatSessionSummary> {
  const { data } = await apiClient.post<ChatSessionSummary>("/chat/sessions", {
    title: title ?? "",
    space_id: spaceId ?? null,
  });
  return data;
}

/** 답변(assistant 메시지)을 위키 페이지로 승격한다. 대상 스페이스 쓰기 자격 필요(403). */
export interface PromoteResult {
  file_id: number;
  name: string;
}

export async function promoteMessage(
  messageId: number,
  payload: { space_id: number; title: string },
): Promise<PromoteResult> {
  const { data } = await apiClient.post<PromoteResult>(
    `/chat/messages/${messageId}/promote`,
    payload,
  );
  return data;
}

export async function listSessions(): Promise<ChatSessionSummary[]> {
  const { data } = await apiClient.get<ChatSessionSummary[]>("/chat/sessions");
  return data;
}

export async function getSession(id: number): Promise<ChatSessionDetail> {
  const { data } = await apiClient.get<ChatSessionDetail>(`/chat/sessions/${id}`);
  return data;
}

export async function deleteSession(id: number): Promise<void> {
  await apiClient.delete(`/chat/sessions/${id}`);
}

/** done 이벤트 페이로드 — title 은 첫 질문 후 자동 설정된 세션 제목. */
export interface ChatDoneData {
  session_id: number;
  message_id: number;
  title: string;
}

export interface StreamHandlers {
  onToken: (value: string) => void;
  onCitations: (citations: ChatCitation[]) => void;
  onDone: (data: ChatDoneData) => void;
  /** error 이벤트 또는 SSE 시작 전 HTTP 오류(503 등). */
  onError: (message: string) => void;
}

/** SSE 프레임 한 개(event/data 라인)를 해석해 콜백으로 디스패치한다. */
function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      // SSE 규약상 'data:' 뒤 선행 공백 1개는 제거한다.
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return;

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    return; // 파싱 불가 프레임은 무시(주석/keep-alive 등).
  }

  switch (event) {
    case "token":
      handlers.onToken(String((parsed as { value?: unknown }).value ?? ""));
      break;
    case "citations":
      handlers.onCitations((parsed as ChatCitation[]) ?? []);
      break;
    case "done":
      handlers.onDone(parsed as ChatDoneData);
      break;
    case "error":
      handlers.onError(
        String((parsed as { detail?: unknown }).detail ?? "답변 생성 중 오류가 발생했습니다."),
      );
      break;
  }
}

/**
 * 질문 전송 → SSE 스트림을 파싱해 콜백으로 흘려보낸다. 청크 경계가 이벤트 경계와 어긋날 수
 * 있으므로 버퍼에 모아 `\n\n` 구분 프레임 단위로 처리한다. signal.abort() 로 중단하면 조용히
 * 반환한다(호출부가 중단 UI 를 관리). SSE 시작 전 HTTP 오류(401/404/503)는 onError 로 전달한다.
 */
export async function streamMessage(
  sessionId: number,
  content: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const token = getAccessToken();
  let res: Response;
  try {
    res = await fetch(`/api/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ content }),
      signal,
    });
  } catch (err) {
    if (isAbort(err)) return;
    handlers.onError("서버에 연결하지 못했습니다.");
    return;
  }

  if (!res.ok || !res.body) {
    handlers.onError(await readErrorDetail(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (frame.trim() !== "") dispatchFrame(frame, handlers);
      }
    }
  } catch (err) {
    if (isAbort(err)) return;
    throw err;
  }
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** 오류 응답 본문에서 detail 을 뽑는다(없으면 상태별 기본 메시지). */
async function readErrorDetail(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* JSON 이 아니면 기본 메시지 */
  }
  if (res.status === 503) return "챗봇이 아직 구성되지 않았습니다. 관리자에게 문의하세요.";
  if (res.status === 404) return "세션을 찾을 수 없습니다.";
  return "답변을 생성하지 못했습니다.";
}
