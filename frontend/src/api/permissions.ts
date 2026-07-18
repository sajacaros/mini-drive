/** 파일 그룹 권한 + 공유된 항목 API (PRD 6.5/6.6). */

import apiClient from "./client";
import type {
  DirectPermission,
  FilePermissions,
  GroupPermissionLevel,
  PermissionCheck,
  SharedWithMeResponse,
} from "./types";

/** 파일 권한 목록 (직접 부여 + 유효 상속) — manage 권한자만. */
export async function listFilePermissions(fileId: number): Promise<FilePermissions> {
  const { data } = await apiClient.get<FilePermissions>(`/files/${fileId}/permissions`);
  return data;
}

export interface GrantPermissionPayload {
  group_id: number;
  permission: GroupPermissionLevel;
  inherit_to_children?: boolean;
  expires_at?: string | null;
}

/** 그룹 권한 부여 ((file,group) 중복은 upsert). */
export async function grantPermission(
  fileId: number,
  payload: GrantPermissionPayload,
): Promise<DirectPermission> {
  const { data } = await apiClient.post<DirectPermission>(
    `/files/${fileId}/permissions`,
    payload,
  );
  return data;
}

export interface UpdatePermissionPayload {
  permission?: GroupPermissionLevel;
  inherit_to_children?: boolean;
  expires_at?: string | null;
}

/** 그룹 권한 수정 (부분 갱신). expires_at 에 null 을 명시하면 만료 해제. */
export async function updatePermission(
  fileId: number,
  groupId: number,
  payload: UpdatePermissionPayload,
): Promise<DirectPermission> {
  const { data } = await apiClient.put<DirectPermission>(
    `/files/${fileId}/permissions/${groupId}`,
    payload,
  );
  return data;
}

/** 그룹 권한 회수. */
export async function revokePermission(fileId: number, groupId: number): Promise<void> {
  await apiClient.delete(`/files/${fileId}/permissions/${groupId}`);
}

/** 내 유효 권한 조회 (owner=manage, 그 외 그룹 권한 판정). */
export async function checkPermission(fileId: number): Promise<PermissionCheck> {
  const { data } = await apiClient.get<PermissionCheck>(`/permissions/check/${fileId}`);
  return data;
}

/** 내 소속 그룹에 공유된 항목(부여 지점) 목록. */
export async function listSharedWithMe(): Promise<SharedWithMeResponse> {
  const { data } = await apiClient.get<SharedWithMeResponse>("/files/shared-with-me");
  return data;
}
