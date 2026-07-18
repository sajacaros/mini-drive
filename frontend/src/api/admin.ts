/** Admin 사용자 관리 API (PRD 6.7). */

import apiClient from "./client";
import type { AdminUser, AdminUserListResponse, UserRole, UserStatus } from "./types";

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

export async function approveUser(id: number): Promise<AdminUser> {
  const { data } = await apiClient.post<AdminUser>(`/admin/users/${id}/approve`);
  return data;
}

export async function rejectUser(id: number): Promise<AdminUser> {
  const { data } = await apiClient.post<AdminUser>(`/admin/users/${id}/reject`);
  return data;
}

export interface UserUpdatePayload {
  status?: UserStatus;
  role?: UserRole;
  max_storage?: number;
}

export async function updateUser(id: number, payload: UserUpdatePayload): Promise<AdminUser> {
  const { data } = await apiClient.patch<AdminUser>(`/admin/users/${id}`, payload);
  return data;
}
