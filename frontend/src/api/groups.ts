/** 그룹/멤버 관리 API (PRD 6.4). */

import apiClient from "./client";
import type {
  Group,
  GroupDetail,
  GroupListResponse,
  GroupMember,
  GroupRole,
} from "./types";

export async function listGroups(page = 1, size = 50): Promise<GroupListResponse> {
  const { data } = await apiClient.get<GroupListResponse>("/groups", {
    params: { page, size },
  });
  return data;
}

export async function getGroup(id: number): Promise<GroupDetail> {
  const { data } = await apiClient.get<GroupDetail>(`/groups/${id}`);
  return data;
}

export async function createGroup(name: string, description?: string): Promise<Group> {
  const { data } = await apiClient.post<Group>("/groups", {
    name,
    description: description || null,
  });
  return data;
}

export async function updateGroup(
  id: number,
  payload: { name?: string; description?: string | null },
): Promise<Group> {
  const { data } = await apiClient.put<Group>(`/groups/${id}`, payload);
  return data;
}

export async function deleteGroup(id: number): Promise<void> {
  await apiClient.delete(`/groups/${id}`);
}

/** 그룹원 초대 (owner/admin). role 은 admin|member (owner 직접 부여 불가). */
export async function inviteMember(
  groupId: number,
  userId: number,
  role: Exclude<GroupRole, "owner"> = "member",
): Promise<GroupMember> {
  const { data } = await apiClient.post<GroupMember>(`/groups/${groupId}/members`, {
    user_id: userId,
    role,
  });
  return data;
}

export async function removeMember(groupId: number, userId: number): Promise<void> {
  await apiClient.delete(`/groups/${groupId}/members/${userId}`);
}

/** 그룹원 역할 변경 (owner 만, admin|member). */
export async function changeMemberRole(
  groupId: number,
  userId: number,
  role: Exclude<GroupRole, "owner">,
): Promise<GroupMember> {
  const { data } = await apiClient.put<GroupMember>(
    `/groups/${groupId}/members/${userId}/role`,
    { role },
  );
  return data;
}

/** 소유권 이전 (owner 만, 대상은 활성 멤버). */
export async function transferOwnership(groupId: number, userId: number): Promise<Group> {
  const { data } = await apiClient.post<Group>(`/groups/${groupId}/transfer-ownership`, {
    user_id: userId,
  });
  return data;
}
