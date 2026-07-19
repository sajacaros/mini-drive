/**
 * 위키 API (PRD 6.8, Phase 7-3). 스페이스/소스/잡/Lint 만 담당한다 —
 * 위키 **페이지** 자체는 드라이브 파일이므로 조회/버전/미리보기는 기존 파일 API(6.2)를 쓴다.
 */

import apiClient from "./client";

export type WikiScope = "personal" | "group";

/** 소스 인제스트 상태 — queued(대기)/indexed(완료)/stale(원본 갱신됨)/failed(실패). */
export type WikiSourceStatus = "queued" | "indexed" | "stale" | "failed";

/** 스페이스 요약 (목록/생성 응답). */
export interface WikiSpace {
  id: number;
  name: string;
  scope: WikiScope;
  user_id: number | null;
  group_id: number | null;
  root_folder_id: number;
  created_at: string;
}

/** 등록 소스 한 건. */
export interface WikiSource {
  file_id: number;
  file_name: string;
  recursive: boolean;
  status: string;
  last_ingested_version: number | null;
}

/** 스페이스 상세 — 소스 목록·상태, 최근 log.md 항목, index.md 카탈로그 요약. */
export interface WikiSpaceDetail extends WikiSpace {
  sources: WikiSource[];
  recent_log: string[];
  index_entries: string[];
}

/** 위키 잡 이력 한 건. */
export interface WikiJob {
  id: number;
  kind: string;
  status: string;
  file_id: number | null;
  retries: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

/** Lint 결과 — 결정적 자동 수정 + 휴리스틱 리포트. */
export interface WikiLintResult {
  auto_fixed: string[];
  reports: string[];
}

export async function listSpaces(): Promise<WikiSpace[]> {
  const { data } = await apiClient.get<WikiSpace[]>("/wiki/spaces");
  return data;
}

export async function getSpace(id: number): Promise<WikiSpaceDetail> {
  const { data } = await apiClient.get<WikiSpaceDetail>(`/wiki/spaces/${id}`);
  return data;
}

export async function createSpace(payload: {
  name: string;
  scope: WikiScope;
  group_id?: number | null;
}): Promise<WikiSpace> {
  const { data } = await apiClient.post<WikiSpace>("/wiki/spaces", {
    name: payload.name,
    scope: payload.scope,
    group_id: payload.group_id ?? null,
  });
  return data;
}

export async function deleteSpace(id: number): Promise<void> {
  await apiClient.delete(`/wiki/spaces/${id}`);
}

export async function addSource(
  spaceId: number,
  payload: { file_id: number; recursive: boolean },
): Promise<WikiSource> {
  const { data } = await apiClient.post<WikiSource>(
    `/wiki/spaces/${spaceId}/sources`,
    payload,
  );
  return data;
}

export async function removeSource(spaceId: number, fileId: number): Promise<void> {
  await apiClient.delete(`/wiki/spaces/${spaceId}/sources/${fileId}`);
}

export async function runLint(spaceId: number): Promise<WikiLintResult> {
  const { data } = await apiClient.post<WikiLintResult>(`/wiki/spaces/${spaceId}/lint`);
  return data;
}

export async function listJobs(spaceId: number): Promise<WikiJob[]> {
  const { data } = await apiClient.get<WikiJob[]>(`/wiki/spaces/${spaceId}/jobs`);
  return data;
}
