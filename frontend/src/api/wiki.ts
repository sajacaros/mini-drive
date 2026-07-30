/** 위키 인덱싱·질의 API (spec/wiki-index.md). */

import apiClient from "./client";
import type {
  WikiAnswer,
  WikiCatalog,
  WikiDocumentList,
  WikiState,
} from "./types";

/** 파일/폴더의 위키 상태 — 소유자·manage 만. 권한이 없으면 404. */
export async function getWikiState(fileId: number): Promise<WikiState> {
  const { data } = await apiClient.get<WikiState>(`/files/${fileId}/wiki`);
  return data;
}

/**
 * 위키 설정 변경.
 *
 * 축은 하나다 — 켜면 색인과 전사 공개(`@전사 read`)가 함께 걸린다. 예전에 있던 `public`
 * 필드는 없앴다(spec/wiki-index.md 「왜 스위치가 하나인가」).
 * `enabled: null` 은 "명시값을 지우고 상속으로 되돌린다"는 뜻이라 생략과 구분된다.
 */
export interface WikiSetPayload {
  enabled: boolean | null;
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

/**
 * 전사 위키의 문서 목록 (인덱싱 전 pending 항목도 포함). 사람마다 다르지 않다.
 *
 * `query` 는 문서명 부분 일치. 수백 건 규모에서는 이름순 페이지를 넘겨 찾을 수 없으므로,
 * "방금 켠 내 문서가 색인됐는가"를 확인하는 경로가 사실상 이것이다.
 */
export async function listWikiDocuments(
  page = 1,
  size = 50,
  query?: string,
): Promise<WikiDocumentList> {
  const { data } = await apiClient.get<WikiDocumentList>("/wiki/documents", {
    params: { page, size, ...(query ? { q: query } : {}) },
  });
  return data;
}

/**
 * 문서 한 건의 카탈로그(절 트리).
 *
 * 질의가 절을 고를 때 보는 트리와 같은 것을 보여준다 — 답이 이상할 때 원문이 아니라 이 트리를
 * 봐야 원인이 보인다. 위키 문서가 아닌 대상만 404 다(꺼진 문서는 상태와 함께 열린다).
 */
export async function getWikiCatalog(fileId: number): Promise<WikiCatalog> {
  const { data } = await apiClient.get<WikiCatalog>(`/wiki/documents/${fileId}`);
  return data;
}

/** 위키 질의 — 검색 대상은 전사 위키 전체다. 문서가 많으면 관련성 순으로 좁혀 본다. */
export async function askWiki(question: string): Promise<WikiAnswer> {
  const { data } = await apiClient.post<WikiAnswer>("/wiki/ask", { question });
  return data;
}
