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
