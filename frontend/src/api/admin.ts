/** Admin 사용자 관리 + 대시보드 API (PRD 6.7). */

import apiClient from "./client";
import type {
  AdminAuditLogListResponse,
  AdminGroupListResponse,
  AdminShare,
  AdminShareListResponse,
  AdminStats,
  AdminUser,
  AdminUserListResponse,
  UserRole,
  UserStatus,
} from "./types";

export async function listUsers(
  status: UserStatus | undefined,
  page = 1,
  size = 20,
): Promise<AdminUserListResponse> {
  const { data } = await apiClient.get<AdminUserListResponse>("/admin/users", {
    params: { status, page, size },
  });
  return data;
}

export interface UserUpdatePayload {
  status?: UserStatus;
  role?: UserRole;
  max_storage?: number;
  display_name?: string;
}

export async function updateUser(id: number, payload: UserUpdatePayload): Promise<AdminUser> {
  const { data } = await apiClient.patch<AdminUser>(`/admin/users/${id}`, payload);
  return data;
}

// --- 대시보드 통계 ----------------------------------------------------------

export async function getStats(): Promise<AdminStats> {
  const { data } = await apiClient.get<AdminStats>("/admin/stats");
  return data;
}

// --- 전체 그룹 --------------------------------------------------------------

export async function listGroups(
  includeInactive = false,
  page = 1,
  size = 20,
): Promise<AdminGroupListResponse> {
  const { data } = await apiClient.get<AdminGroupListResponse>("/admin/groups", {
    params: { include_inactive: includeInactive, page, size },
  });
  return data;
}

// --- 전체 공유 링크 ---------------------------------------------------------

export async function listShares(
  active: boolean | undefined,
  userId: number | undefined,
  page = 1,
  size = 20,
): Promise<AdminShareListResponse> {
  const { data } = await apiClient.get<AdminShareListResponse>("/admin/shares", {
    params: { active, userId, page, size },
  });
  return data;
}

/** 공유 링크 강제 비활성화 (멱등). */
export async function disableShare(id: number): Promise<AdminShare> {
  const { data } = await apiClient.post<AdminShare>(`/admin/shares/${id}/disable`);
  return data;
}

// --- 감사 로그 --------------------------------------------------------------

export interface AuditLogFilter {
  actorId?: number;
  targetType?: string;
  action?: string;
  from?: string;
  to?: string;
}

export async function listAuditLogs(
  filter: AuditLogFilter,
  page = 1,
  size = 20,
): Promise<AdminAuditLogListResponse> {
  const { data } = await apiClient.get<AdminAuditLogListResponse>("/admin/audit-logs", {
    params: {
      actorId: filter.actorId,
      targetType: filter.targetType,
      action: filter.action,
      from: filter.from,
      to: filter.to,
      page,
      size,
    },
  });
  return data;
}
