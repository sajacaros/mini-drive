/**
 * 대화형 질의 API.
 *
 * 단발 `POST /wiki/ask`(api/wiki.ts 의 askWiki)와 나란히 존재한다 — 그쪽은 세션 없이 한 번
 * 묻고 마는 경로이고, 이쪽은 맥락이 이어지는 대화다.
 */

import apiClient from "./client";
import type {
  ChatAskResponse,
  ChatSessionDetail,
  ChatSessionItem,
  ChatSessionList,
} from "./types";

/** 새 대화를 연다. 제목은 첫 질문에서 서버가 자동으로 만든다. */
export async function createChatSession(title = ""): Promise<ChatSessionItem> {
  const { data } = await apiClient.post<ChatSessionItem>("/chat/sessions", { title });
  return data;
}

/** 내 대화 목록 — 최근 대화순. 아직 대화가 없는 세션이 맨 앞에 온다. */
export async function listChatSessions(page = 1, size = 50): Promise<ChatSessionList> {
  const { data } = await apiClient.get<ChatSessionList>("/chat/sessions", {
    params: { page, size },
  });
  return data;
}

/** 대화 한 건 — 메시지 전체를 포함한다. */
export async function getChatSession(sessionId: number): Promise<ChatSessionDetail> {
  const { data } = await apiClient.get<ChatSessionDetail>(`/chat/sessions/${sessionId}`);
  return data;
}

export async function renameChatSession(
  sessionId: number,
  title: string,
): Promise<ChatSessionItem> {
  const { data } = await apiClient.patch<ChatSessionItem>(`/chat/sessions/${sessionId}`, {
    title,
  });
  return data;
}

/** 소프트 삭제 — 목록에서만 사라진다. */
export async function deleteChatSession(sessionId: number): Promise<void> {
  await apiClient.delete(`/chat/sessions/${sessionId}`);
}

/**
 * 질문하고 답을 받는다.
 *
 * 질문과 답변은 서버에서 한 트랜잭션으로 저장되므로, 실패하면 **둘 다** 남지 않는다.
 * 화면이 낙관적으로 그려 둔 질문 말풍선을 그때 거둬들여야 한다.
 */
export async function askChat(
  sessionId: number,
  question: string,
): Promise<ChatAskResponse> {
  const { data } = await apiClient.post<ChatAskResponse>(
    `/chat/sessions/${sessionId}/messages`,
    { question },
  );
  return data;
}
