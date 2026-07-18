/**
 * 백엔드 API 응답/요청 타입 (backend/app/schemas 와 1:1 대응).
 * 진실 소스는 backend 스키마이며, 여기서는 프론트 소비에 필요한 형태만 미러링한다.
 */

export type UserStatus = "pending" | "active" | "inactive" | "rejected";
export type UserRole = "user" | "admin";

/** GET /api/auth/me, 그리고 login/register 이후 사용자 정보. */
export interface User {
  id: number;
  email: string;
  display_name: string;
  avatar_url: string | null;
  role: UserRole;
  status: UserStatus;
  storage_used: number;
  max_storage: number;
  created_at: string;
  updated_at: string;
}

/** POST /api/auth/login, /api/auth/refresh. */
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

/** POST /api/auth/register. */
export interface RegisterResponse {
  id: number;
  email: string;
  status: UserStatus;
  message: string;
}

/** 파일 또는 폴더 메타데이터 (GET /api/files, /api/files/{id}). */
export interface FileNode {
  id: number;
  user_id: number;
  parent_folder_id: number | null;
  name: string;
  mime_type: string | null;
  size: number;
  is_folder: boolean;
  is_deleted: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

/** GET /api/files (페이지네이션). */
export interface FileListResponse {
  items: FileNode[];
  total: number;
  page: number;
  size: number;
}

/** 파일 버전 히스토리 항목 (GET /api/files/{id}/versions). */
export interface FileVersion {
  version: number;
  size: number;
  mime_type: string | null;
  uploaded_by: number;
  uploaded_by_name: string;
  uploaded_at: string;
  is_current: boolean;
}

/** 버전 목록 응답 (내림차순). */
export interface FileVersionListResponse {
  file_id: number;
  current_version: number;
  items: FileVersion[];
}

/** 단기 1회용 다운로드 티켓 (POST .../download-ticket). url 을 무헤더 GET 하면 스트리밍. */
export interface DownloadTicketResponse {
  ticket: string;
  url: string;
  expires_in: number;
}

export type SharePermission = "read" | "download";

/** 소유자용 공유 링크 응답 (POST/GET /api/shares). */
export interface Share {
  id: number;
  file_id: number;
  file_name: string;
  share_url: string;
  permission: string;
  is_active: boolean;
  password_required: boolean;
  expires_at: string | null;
  max_downloads: number | null;
  download_count: number;
  created_at: string;
}

export interface ShareCreateRequest {
  file_id: number;
  permission?: SharePermission;
  expires_at?: string | null;
  password?: string | null;
  max_downloads?: number | null;
}

/** 공개(무인증) 공유 메타 (GET /api/public/shares/{shareUrl}). */
export interface SharePublicMeta {
  file_name: string;
  size: number;
  mime_type: string | null;
  permission: string;
  password_required: boolean;
  expires_at: string | null;
}

/** admin 사용자 목록 항목 (GET /api/admin/users). */
export interface AdminUser {
  id: number;
  email: string;
  display_name: string;
  avatar_url: string | null;
  role: UserRole;
  status: UserStatus;
  storage_used: number;
  max_storage: number;
  created_at: string;
  updated_at: string;
}

export interface AdminUserListResponse {
  items: AdminUser[];
  total: number;
  page: number;
  size: number;
}

// --- 그룹 (PRD 6.4) --------------------------------------------------------

export type GroupRole = "owner" | "admin" | "member";

/** 그룹 기본 정보. */
export interface Group {
  id: number;
  name: string;
  description: string | null;
  owner_user_id: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** 그룹 목록 항목 — 멤버 수 + 내 역할 포함 (GET /api/groups). */
export interface GroupSummary extends Group {
  member_count: number;
  my_role: GroupRole | null;
}

export interface GroupListResponse {
  items: GroupSummary[];
  total: number;
  page: number;
  size: number;
}

/** 활성 그룹원. */
export interface GroupMember {
  user_id: number;
  email: string;
  display_name: string;
  role: GroupRole;
  joined_at: string;
}

/** 그룹 상세 — 기본 정보 + 멤버 목록 (GET /api/groups/{id}). */
export interface GroupDetail extends GroupSummary {
  members: GroupMember[];
}

// --- 권한 (PRD 6.5/6.6) ----------------------------------------------------

export type GroupPermissionLevel = "read" | "write" | "manage";

/** 파일에 직접 부여된 그룹 권한. */
export interface DirectPermission {
  group_id: number;
  group_name: string;
  permission: string;
  inherit_to_children: boolean;
  granted_at: string;
  expires_at: string | null;
  granted_by: number;
}

/** 조상 폴더에서 상속되어 유효한 그룹 권한. */
export interface InheritedPermission {
  group_id: number;
  group_name: string;
  permission: string;
  source_file_id: number;
  source_file_name: string;
  depth: number;
  expires_at: string | null;
}

/** 파일 권한 목록 — 직접 부여 + 유효 상속 (GET /api/files/{id}/permissions). */
export interface FilePermissions {
  file_id: number;
  direct: DirectPermission[];
  inherited: InheritedPermission[];
}

/** 내 유효 권한 (GET /api/permissions/check/{fileId}). */
export interface PermissionCheck {
  file_id: number;
  permission: "read" | "write" | "manage" | "none";
  via: "owner" | "group" | "admin" | "none";
  source_file_id: number | null;
}

/** 공유된 항목(부여 지점) 하나 (GET /api/files/shared-with-me). */
export interface SharedItem {
  file: FileNode;
  group_id: number;
  group_name: string;
  permission: string;
}

export interface SharedWithMeResponse {
  items: SharedItem[];
}
