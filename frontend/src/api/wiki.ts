/** 위키 인덱싱·질의 API (spec/wiki-index.md). */

import apiClient from "./client";
import type {
  WikiAnswer,
  WikiDocumentList,
  WikiState,
} from "./types";

/** 파일/폴더의 위키 상태 — 소유자·manage 만. 권한이 없으면 404. */
export async function getWikiState(fileId: number): Promise<WikiState> {
  const { data } = await apiClient.get<WikiState>(`/files/${fileId}/wiki`);
  return data;
}

/**
 * 위키 설정 변경 (부분 갱신).
 *
 * 두 축은 독립이라 각각 생략할 수 있다 — `public` 만 보내면 인덱싱 설정은 그대로다.
 * `enabled: null` 은 "명시값을 지우고 상속으로 되돌린다"는 뜻이라 생략과 구분된다.
 */
export interface WikiSetPayload {
  enabled?: boolean | null;
  public?: boolean;
}

export async function setWiki(
  fileId: number,
  payload: WikiSetPayload,
): Promise<WikiState> {
  const { data } = await apiClient.put<WikiState>(`/files/${fileId}/wiki`, payload);
  return data;
}

/**
 * 트리를 즉시 삭제한다 (유예를 기다리지 않음).
 *
 * 위키를 끄면 트리는 재켜기 비용 때문에 유예 기간 동안 보관된다. 규정상 파생물이 남으면 안
 * 되는 경우를 위한 탈출구다.
 */
export async function purgeWikiTree(fileId: number): Promise<void> {
  await apiClient.delete(`/files/${fileId}/wiki/tree`);
}

/** 내가 접근할 수 있는 위키 문서 목록 (인덱싱 전 pending 항목도 포함). */
export async function listWikiDocuments(
  page = 1,
  size = 50,
): Promise<WikiDocumentList> {
  const { data } = await apiClient.get<WikiDocumentList>("/wiki/documents", {
    params: { page, size },
  });
  return data;
}

/** 위키 질의 — 검색 대상은 내가 접근 가능한 문서로 한정된다. */
export async function askWiki(question: string): Promise<WikiAnswer> {
  const { data } = await apiClient.post<WikiAnswer>("/wiki/ask", { question });
  return data;
}
