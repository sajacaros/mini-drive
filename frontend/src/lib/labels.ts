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
