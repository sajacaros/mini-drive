/** 그룹 역할·권한 수준의 한국어 라벨 + 배지 톤 매핑 (UI 공용). */

import type { GroupPermissionLevel, GroupRole } from "@/api/types";

type Tone = "neutral" | "success" | "warning" | "danger" | "accent";

const ROLE_LABELS: Record<GroupRole, string> = {
  owner: "소유자",
  admin: "관리자",
  member: "멤버",
};

const ROLE_TONES: Record<GroupRole, Tone> = {
  owner: "accent",
  admin: "warning",
  member: "neutral",
};

export function roleLabel(role: GroupRole | string | null): string {
  if (!role) return "-";
  return ROLE_LABELS[role as GroupRole] ?? role;
}

export function roleTone(role: GroupRole | string | null): Tone {
  if (!role) return "neutral";
  return ROLE_TONES[role as GroupRole] ?? "neutral";
}

const PERMISSION_LABELS: Record<GroupPermissionLevel, string> = {
  read: "읽기",
  write: "쓰기",
  manage: "관리",
};

const PERMISSION_TONES: Record<GroupPermissionLevel, Tone> = {
  read: "neutral",
  write: "accent",
  manage: "warning",
};

export function permissionLabel(permission: string): string {
  return PERMISSION_LABELS[permission as GroupPermissionLevel] ?? permission;
}

export function permissionTone(permission: string): Tone {
  return PERMISSION_TONES[permission as GroupPermissionLevel] ?? "neutral";
}

/** 권한 수준 순서 (read < write < manage). */
const PERMISSION_RANK: Record<string, number> = {
  none: 0,
  read: 1,
  write: 2,
  manage: 3,
};

/** level 이 need 이상인지 (read/write/manage 포함 관계). */
export function permissionCovers(level: string, need: GroupPermissionLevel): boolean {
  return (PERMISSION_RANK[level] ?? 0) >= (PERMISSION_RANK[need] ?? 99);
}

// --- 전역 역할 (user/admin/super_admin, 그룹 role 과 별개 축) ----------------

const GLOBAL_ROLE_LABELS: Record<string, string> = {
  user: "일반",
  admin: "관리자",
  super_admin: "최고 관리자",
};

const GLOBAL_ROLE_TONES: Record<string, Tone> = {
  user: "neutral",
  admin: "accent",
  super_admin: "warning",
};

export function globalRoleLabel(role: string): string {
  return GLOBAL_ROLE_LABELS[role] ?? role;
}

export function globalRoleTone(role: string): Tone {
  return GLOBAL_ROLE_TONES[role] ?? "neutral";
}

// --- 사용자 상태 (admin UI 공용) -------------------------------------------

const USER_STATUS_LABELS: Record<string, string> = {
  active: "활성",
  inactive: "비활성",
};

const USER_STATUS_TONES: Record<string, Tone> = {
  active: "success",
  inactive: "neutral",
};

export function userStatusLabel(status: string): string {
  return USER_STATUS_LABELS[status] ?? status;
}

export function userStatusTone(status: string): Tone {
  return USER_STATUS_TONES[status] ?? "neutral";
}

// --- 공유 링크 권한 (admin UI 공용) ----------------------------------------

const SHARE_PERMISSION_LABELS: Record<string, string> = {
  read: "읽기",
  download: "다운로드",
};

export function sharePermissionLabel(permission: string): string {
  return SHARE_PERMISSION_LABELS[permission] ?? permission;
}

// --- 감사 로그 (admin UI 공용) ---------------------------------------------

const AUDIT_ACTION_LABELS: Record<string, string> = {
  "user.approve": "가입 승인",
  "user.reject": "가입 거절",
  "user.activate": "활성화",
  "user.deactivate": "비활성화",
  "user.quota_update": "할당량 변경",
  "user.role_update": "역할 변경",
  "share.force_disable": "공유 강제 차단",
  "permission.grant": "권한 부여",
  "permission.revoke": "권한 회수",
  "signup_code.create": "가입 코드 발급",
  "signup_code.update": "가입 코드 수정",
};

export function auditActionLabel(action: string): string {
  return AUDIT_ACTION_LABELS[action] ?? action;
}

const AUDIT_ACTION_TONES: Record<string, Tone> = {
  approve: "success",
  activate: "success",
  grant: "success",
  reject: "danger",
  deactivate: "danger",
  force_disable: "danger",
  revoke: "danger",
  quota_update: "accent",
  role_update: "accent",
  create: "success",
  update: "accent",
};

export function auditActionTone(action: string): Tone {
  const verb = action.split(".")[1] ?? action;
  return AUDIT_ACTION_TONES[verb] ?? "neutral";
}

const TARGET_TYPE_LABELS: Record<string, string> = {
  user: "사용자",
  group: "그룹",
  file: "파일",
  share: "공유",
  signup_code: "가입 코드",
};

export function targetTypeLabel(targetType: string): string {
  return TARGET_TYPE_LABELS[targetType] ?? targetType;
}
