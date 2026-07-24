/** 공유 링크 API (PRD 6.3). */

import { withBase } from "@/lib/basePath";
import apiClient from "./client";
import type {
  Share,
  ShareCreateRequest,
  ShareFolderListing,
  ShareListResponse,
  SharePublicMeta,
  ShareStats,
} from "./types";

export async function createShare(payload: ShareCreateRequest): Promise<Share> {
  const { data } = await apiClient.post<Share>("/shares", payload);
  return data;
}

/** 내 공유 링크 목록. active 로 활성/비활성 탭을 나눈다(생략 시 전체). */
export async function listShares(
  active?: boolean,
  page = 1,
  size = 20,
): Promise<ShareListResponse> {
  const { data } = await apiClient.get<ShareListResponse>("/shares", {
    params: { active, page, size },
  });
  return data;
}

/** 단건 공유 통계 (PRD 3.4). download_count 는 DB 정확값, view/last_access 는 Redis 근사치. */
export async function getShareStats(id: number): Promise<ShareStats> {
  const { data } = await apiClient.get<ShareStats>(`/shares/${id}/stats`);
  return data;
}

export async function disableShare(id: number): Promise<void> {
  await apiClient.delete(`/shares/${id}`);
}

/**
 * 공개 공유 메타 조회 (무인증). 인증 헤더가 붙지 않도록 apiClient 대신 순수 fetch 사용.
 * 404(없음)/410(만료·비활성) 을 status 로 구분해 던진다.
 */
export class ShareMetaError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function throwShareMetaError(res: Response, fallback: string): Promise<never> {
  let detail = fallback;
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") detail = data.detail;
  } catch {
    /* 기본 메시지 유지 */
  }
  throw new ShareMetaError(res.status, detail);
}

export async function fetchPublicShareMeta(shareUrl: string): Promise<SharePublicMeta> {
  const res = await fetch(withBase(`/api/public/shares/${shareUrl}`));
  if (!res.ok) await throwShareMetaError(res, "공유 정보를 불러오지 못했습니다.");
  return (await res.json()) as SharePublicMeta;
}

/**
 * 폴더 공유 웹 탐색 목록 (무인증). folderId 생략 시 공유 루트. 비밀번호가 걸린 공유는
 * password 를 넘겨야 하며, 오류(401 비밀번호 / 404 트리 밖 / 410 만료)는 ShareMetaError 로
 * 던져 호출부가 상태별 안내를 하게 한다.
 */
export async function fetchPublicShareListing(
  shareUrl: string,
  folderId?: number,
  password?: string,
): Promise<ShareFolderListing> {
  const res = await fetch(withBase(`/api/public/shares/${shareUrl}/list`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: password ?? null, folder_id: folderId ?? null }),
  });
  if (!res.ok) await throwShareMetaError(res, "폴더 목록을 불러오지 못했습니다.");
  return (await res.json()) as ShareFolderListing;
}
