/** 파일/폴더 그룹 권한 관리 모달 — 직접 부여 목록 + 상속 목록 표시, 부여/수정/회수. */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { listGroups } from "@/api/groups";
import {
  grantPermission,
  listFilePermissions,
  revokePermission,
  updatePermission,
} from "@/api/permissions";
import type {
  DirectPermission,
  FileNode,
  GroupPermissionLevel,
  GroupSummary,
  InheritedPermission,
} from "@/api/types";
import { Modal } from "./Modal";
import { Badge, LoadingState, Spinner } from "./ui";
import { useToast } from "./Toast";
import { formatDateTime } from "@/lib/format";
import { permissionLabel, permissionTone } from "@/lib/labels";

export function PermissionModal({
  file,
  open,
  onClose,
}: {
  file: FileNode | null;
  open: boolean;
  onClose: () => void;
}) {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [direct, setDirect] = useState<DirectPermission[]>([]);
  const [inherited, setInherited] = useState<InheritedPermission[]>([]);
  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [busyGroup, setBusyGroup] = useState<number | null>(null);

  // 부여 폼
  const [groupId, setGroupId] = useState("");
  const [level, setLevel] = useState<GroupPermissionLevel>("read");
  const [inherit, setInherit] = useState(true);
  const [expiresAt, setExpiresAt] = useState("");
  const [granting, setGranting] = useState(false);

  const load = useCallback(async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const [perms, groupList] = await Promise.all([
        listFilePermissions(file.id),
        listGroups(1, 100),
      ]);
      setDirect(perms.direct);
      setInherited(perms.inherited);
      setGroups(groupList.items);
    } catch (err) {
      setError(extractErrorMessage(err, "권한 정보를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [file]);

  useEffect(() => {
    if (open && file) {
      // 폼 초기화 후 로드.
      setGroupId("");
      setLevel("read");
      setInherit(true);
      setExpiresAt("");
      void load();
    }
  }, [open, file, load]);

  const grantedGroupIds = new Set(direct.map((d) => d.group_id));

  const onGrant = async () => {
    if (!file) return;
    const gid = Number(groupId);
    if (!Number.isInteger(gid) || gid <= 0) {
      toast.error("그룹을 선택하세요.");
      return;
    }
    setGranting(true);
    try {
      await grantPermission(file.id, {
        group_id: gid,
        permission: level,
        inherit_to_children: inherit,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
      toast.success("권한을 부여했습니다.");
      setGroupId("");
      setExpiresAt("");
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "권한 부여에 실패했습니다."));
    } finally {
      setGranting(false);
    }
  };

  const onUpdate = async (
    d: DirectPermission,
    patch: { permission?: GroupPermissionLevel; inherit_to_children?: boolean },
  ) => {
    if (!file) return;
    setBusyGroup(d.group_id);
    try {
      await updatePermission(file.id, d.group_id, patch);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "권한 수정에 실패했습니다."));
    } finally {
      setBusyGroup(null);
    }
  };

  const onRevoke = async (d: DirectPermission) => {
    if (!file) return;
    setBusyGroup(d.group_id);
    try {
      await revokePermission(file.id, d.group_id);
      toast.success(`${d.group_name} 그룹의 권한을 회수했습니다.`);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "권한 회수에 실패했습니다."));
    } finally {
      setBusyGroup(null);
    }
  };

  // 아직 직접 부여되지 않은 그룹만 선택지로 노출 (이미 있으면 목록에서 수정).
  const selectableGroups = groups.filter((g) => !grantedGroupIds.has(g.id));

  return (
    <Modal
      open={open}
      title="권한 관리"
      onClose={onClose}
      footer={
        <button className="btn btn-secondary" onClick={onClose}>
          닫기
        </button>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="truncate text-sm text-muted">
          대상: <span className="font-medium text-[color:var(--text-primary)]">{file?.name}</span>
          {file?.is_folder && " (폴더)"}
        </p>

        {loading ? (
          <LoadingState />
        ) : error ? (
          <p className="py-6 text-center text-sm" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        ) : (
          <>
            {/* 직접 부여 목록 */}
            <div>
              <h3 className="mb-2 text-sm font-semibold">직접 부여된 권한</h3>
              {direct.length === 0 ? (
                <p className="rounded-lg bg-muted-token px-3 py-3 text-sm text-muted">
                  직접 부여된 그룹 권한이 없습니다.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {direct.map((d) => (
                    <div key={d.group_id} className="rounded-lg border border-token px-3 py-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium">{d.group_name}</span>
                        <button
                          className="btn btn-ghost px-2 py-1 text-red-600"
                          disabled={busyGroup === d.group_id}
                          onClick={() => onRevoke(d)}
                        >
                          회수
                        </button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        <select
                          className="input w-auto py-1"
                          value={d.permission}
                          disabled={busyGroup === d.group_id}
                          onChange={(e) =>
                            onUpdate(d, {
                              permission: e.target.value as GroupPermissionLevel,
                            })
                          }
                        >
                          <option value="read">읽기</option>
                          <option value="write">쓰기</option>
                          <option value="manage">관리</option>
                        </select>
                        {file?.is_folder && (
                          <label className="flex items-center gap-1.5 text-muted">
                            <input
                              type="checkbox"
                              checked={d.inherit_to_children}
                              disabled={busyGroup === d.group_id}
                              onChange={(e) =>
                                onUpdate(d, { inherit_to_children: e.target.checked })
                              }
                            />
                            하위 상속
                          </label>
                        )}
                        {d.expires_at && (
                          <span className="text-muted">{formatDateTime(d.expires_at)} 만료</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 상속된 권한 */}
            {inherited.length > 0 && (
              <div>
                <h3 className="mb-2 text-sm font-semibold">상속된 권한</h3>
                <div className="flex flex-col gap-1.5">
                  {inherited.map((i) => (
                    <div
                      key={`${i.group_id}-${i.source_file_id}`}
                      className="flex items-center justify-between gap-2 rounded-lg bg-muted-token px-3 py-2 text-sm"
                    >
                      <span className="truncate">
                        {i.group_name}
                        <span className="text-muted"> · {i.source_file_name}에서 상속</span>
                      </span>
                      <Badge tone={permissionTone(i.permission)}>
                        {permissionLabel(i.permission)}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 권한 부여 폼 */}
            <div className="border-t border-token pt-4">
              <h3 className="mb-2 text-sm font-semibold">권한 부여</h3>
              {groups.length === 0 ? (
                <p className="text-sm text-muted">
                  먼저 그룹을 만들어야 권한을 부여할 수 있습니다.
                </p>
              ) : (
                <div className="flex flex-col gap-3">
                  <div>
                    <label className="label" htmlFor="permGroup">
                      그룹
                    </label>
                    <select
                      id="permGroup"
                      className="input"
                      value={groupId}
                      onChange={(e) => setGroupId(e.target.value)}
                    >
                      <option value="">그룹 선택...</option>
                      {selectableGroups.map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                    </select>
                    {selectableGroups.length === 0 && (
                      <p className="mt-1 text-xs text-muted">
                        모든 그룹에 이미 권한이 부여되어 있습니다.
                      </p>
                    )}
                  </div>
                  <div className="flex gap-3">
                    <div className="flex-1">
                      <label className="label" htmlFor="permLevel">
                        권한 수준
                      </label>
                      <select
                        id="permLevel"
                        className="input"
                        value={level}
                        onChange={(e) => setLevel(e.target.value as GroupPermissionLevel)}
                      >
                        <option value="read">읽기 (조회·다운로드)</option>
                        <option value="write">쓰기 (업로드·수정·삭제)</option>
                        <option value="manage">관리 (권한 관리)</option>
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="label" htmlFor="permExpires">
                      만료일 (선택)
                    </label>
                    <input
                      id="permExpires"
                      type="datetime-local"
                      className="input"
                      value={expiresAt}
                      onChange={(e) => setExpiresAt(e.target.value)}
                    />
                  </div>
                  {file?.is_folder && (
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={inherit}
                        onChange={(e) => setInherit(e.target.checked)}
                      />
                      하위 항목에 상속
                    </label>
                  )}
                  <button
                    className="btn btn-primary"
                    onClick={onGrant}
                    disabled={granting || !groupId}
                  >
                    {granting ? <Spinner className="h-4 w-4" /> : "권한 부여"}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
