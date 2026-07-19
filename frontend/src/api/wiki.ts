/**
 * 위키 API (전사 단일 위키, wiki-v2 4.2, Phase 7-4). 개요/소스/잡/Lint/승격만 담당한다 —
 * 위키 **페이지** 자체는 드라이브 파일이므로 조회/버전/미리보기는 기존 파일 API(6.2)를 쓴다.
 *
 * 스페이스 개념은 제거됐다. "위키에 공유" 체크(소유자만) = 전사 출판이며, 모든 로그인 사용자가
 * 같은 위키를 본다.
 */

import apiClient from "./client";

/** 소스 인제스트 상태 — queued(대기)/indexed(완료)/stale(원본 갱신됨)/failed(실패). */
export type WikiSourceStatus = "queued" | "indexed" | "stale" | "failed";

/** 공유 소스 한 건 — 위키에 공유 체크된 파일/폴더. added_by = 파일 소유자(wiki-v2 D1). */
export interface WikiSource {
  file_id: number;
  file_name: string;
  status: string;
  last_ingested_version: number | null;
  added_by: number;
}

/** 위키 개요 (GET /api/wiki) — 루트 폴더, 공유 소스 현황, 최근 log.md, index.md 카탈로그. */
export interface WikiOverview {
  root_folder_id: number;
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

/** 승격 결과 — 생성/갱신된 위키 페이지(드라이브 파일) id 와 이름. */
export interface WikiPromoteResult {
  file_id: number;
  name: string;
}

/** 위키 개요 조회 (로그인 사용자 누구나). */
export async function getWiki(): Promise<WikiOverview> {
  const { data } = await apiClient.get<WikiOverview>("/wiki");
  return data;
}

/** 위키 공유 체크 — 항목 소유자만(403). 폴더는 항상 재귀(하위의 내 소유 파일만). */
export async function addSource(fileId: number): Promise<WikiSource> {
  const { data } = await apiClient.post<WikiSource>("/wiki/sources", {
    file_id: fileId,
  });
  return data;
}

/** 위키 공유 해제 — 소유자만. 페이지는 잔존하고 Lint 실행 시 정리 안내로 표시된다(D5). */
export async function removeSource(fileId: number): Promise<void> {
  await apiClient.delete(`/wiki/sources/${fileId}`);
}

/** Lint 실행 (로그인 사용자 누구나 — 결정적 자동 수정뿐, D6). */
export async function runLint(): Promise<WikiLintResult> {
  const { data } = await apiClient.post<WikiLintResult>("/wiki/lint");
  return data;
}

/** 위키 잡 이력 조회. */
export async function listJobs(): Promise<WikiJob[]> {
  const { data } = await apiClient.get<WikiJob[]>("/wiki/jobs");
  return data;
}

/** 챗 답변을 위키 페이지로 승격한다 (전사 공개, D7). 본인 assistant 메시지만 승격 가능. */
export async function promoteAnswer(payload: {
  message_id: number;
  title: string;
}): Promise<WikiPromoteResult> {
  const { data } = await apiClient.post<WikiPromoteResult>("/wiki/promote", payload);
  return data;
}
