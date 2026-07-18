/** 공유 링크 API (PRD 6.3). */

import apiClient from "./client";
import type { Share, ShareCreateRequest, SharePublicMeta, ShareStats } from "./types";

export async function createShare(payload: ShareCreateRequest): Promise<Share> {
  const { data } = await apiClient.post<Share>("/shares", payload);
  return data;
}

export async function listShares(): Promise<Share[]> {
  const { data } = await apiClient.get<Share[]>("/shares");
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

export async function fetchPublicShareMeta(shareUrl: string): Promise<SharePublicMeta> {
  const res = await fetch(`/api/public/shares/${shareUrl}`);
  if (!res.ok) {
    let detail = "공유 정보를 불러오지 못했습니다.";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* 기본 메시지 유지 */
    }
    throw new ShareMetaError(res.status, detail);
  }
  return (await res.json()) as SharePublicMeta;
}
