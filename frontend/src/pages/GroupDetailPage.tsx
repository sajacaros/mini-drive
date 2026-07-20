import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import {
  changeMemberRole,
  deleteGroup,
  getGroup,
  inviteMember,
  removeMember,
  transferOwnership,
  updateGroup,
} from "@/api/groups";
import { searchUsers } from "@/api/users";
import type { GroupDetail, GroupMember, GroupRole, UserLookup } from "@/api/types";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { Badge, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { ChevronRight, PlusIcon, TrashIcon } from "@/components/icons";
import { formatDate } from "@/lib/format";
import { roleLabel, roleTone } from "@/lib/labels";
import { useAuthStore } from "@/store/auth";

export function GroupDetailPage() {
  const { id } = useParams();
  const groupId = Number(id);
  const toast = useToast();
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.user);

  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [transferTarget, setTransferTarget] = useState<GroupMember | null>(null);
  const [removeTarget, setRemoveTarget] = useState<GroupMember | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setGroup(await getGroup(groupId));
    } catch (err) {
      setError(extractErrorMessage(err, "그룹을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    if (Number.isNaN(groupId)) {
      setError("잘못된 그룹입니다.");
      setLoading(false);
      return;
    }
    void load();
  }, [groupId, load]);

  const myRole = group?.my_role ?? null;
  const isOwner = myRole === "owner";
  const canManageMembers = myRole === "owner" || myRole === "admin";

  /** actor(나)가 대상 멤버를 제거할 수 있는지 — owner 는 owner 외 모두, admin 은 member 만. */
  const canRemove = (m: GroupMember): boolean => {
    if (m.user_id === me?.id) return false; // 자기 자신은 이 화면에서 제거 불가
    if (isOwner) return m.role !== "owner";
    if (myRole === "admin") return m.role === "member";
    return false;
  };

  const onChangeRole = async (m: GroupMember, role: Exclude<GroupRole, "owner">) => {
    if (role === m.role) return;
    setBusy(true);
    try {
      await changeMemberRole(groupId, m.user_id, role);
      toast.success(`${m.display_name}님의 역할을 변경했습니다.`);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "역할 변경에 실패했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    setBusy(true);
    try {
      await removeMember(groupId, removeTarget.user_id);
      toast.success(`${removeTarget.display_name}님을 제거했습니다.`);
      setRemoveTarget(null);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "멤버 제거에 실패했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const confirmTransfer = async () => {
    if (!transferTarget) return;
    setBusy(true);
    try {
      await transferOwnership(groupId, transferTarget.user_id);
      toast.success(`${transferTarget.display_name}님에게 소유권을 넘겼습니다.`);
      setTransferTarget(null);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "소유권 이전에 실패했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    setBusy(true);
    try {
      await deleteGroup(groupId);
      toast.success("그룹을 삭제했습니다.");
      navigate("/groups", { replace: true });
    } catch (err) {
      toast.error(extractErrorMessage(err, "그룹 삭제에 실패했습니다."));
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-screen flex-col">
        <LoadingState />
      </div>
    );
  }

  if (error || !group) {
    return (
      <div className="flex h-screen flex-col p-6">
        <ErrorState message={error ?? "그룹을 찾을 수 없습니다."} onRetry={load} />
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      {/* 헤더: breadcrumb + 그룹명 */}
      <div className="border-b border-token px-6 py-4">
        <nav className="mb-1 flex items-center gap-1 text-sm text-muted">
          <button className="hover:text-[color:var(--text-primary)]" onClick={() => navigate("/groups")}>
            그룹
          </button>
          <ChevronRight width={14} height={14} />
          <span className="truncate font-medium text-[color:var(--text-primary)]">{group.name}</span>
        </nav>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold">{group.name}</h1>
              {myRole && <Badge tone={roleTone(myRole)}>{roleLabel(myRole)}</Badge>}
            </div>
            <p className="mt-0.5 text-sm text-muted">{group.description || "설명 없음"}</p>
          </div>
          <div className="flex shrink-0 gap-2">
            {canManageMembers && (
              <button className="btn btn-secondary" onClick={() => setEditOpen(true)}>
                정보 수정
              </button>
            )}
            {isOwner && (
              <button className="btn btn-ghost text-danger" onClick={() => setDeleteOpen(true)}>
                그룹 삭제
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">멤버 {group.member_count}명</h2>
          {canManageMembers && (
            <button className="btn btn-primary" onClick={() => setInviteOpen(true)}>
              <PlusIcon width={16} height={16} />
              멤버 초대
            </button>
          )}
        </div>

        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-token text-left text-xs text-muted">
                <th className="px-4 py-2.5 font-medium">멤버</th>
                <th className="w-32 px-4 py-2.5 font-medium">역할</th>
                <th className="w-32 px-4 py-2.5 font-medium">가입일</th>
                <th className="w-56 px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {group.members.map((m) => (
                <tr key={m.user_id} className="border-b border-token last:border-0">
                  <td className="px-4 py-2.5">
                    <div className="flex flex-col">
                      <span className="truncate font-medium">
                        {m.display_name}
                        {m.user_id === me?.id && <span className="text-muted"> (나)</span>}
                      </span>
                      <span className="truncate text-xs text-muted">{m.email}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {/* owner 만 역할 변경 가능. owner 자신/owner 역할은 드롭다운 없이 배지. */}
                    {isOwner && m.role !== "owner" ? (
                      <select
                        className="input py-1.5"
                        value={m.role}
                        disabled={busy}
                        onChange={(e) =>
                          onChangeRole(m, e.target.value as Exclude<GroupRole, "owner">)
                        }
                      >
                        <option value="member">멤버</option>
                        <option value="admin">관리자</option>
                      </select>
                    ) : (
                      <Badge tone={roleTone(m.role)}>{roleLabel(m.role)}</Badge>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted">{formatDate(m.joined_at)}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-2">
                      {isOwner && m.role !== "owner" && (
                        <button
                          className="btn btn-ghost"
                          disabled={busy}
                          onClick={() => setTransferTarget(m)}
                        >
                          소유권 이전
                        </button>
                      )}
                      {canRemove(m) && (
                        <button
                          className="btn btn-ghost text-danger"
                          disabled={busy}
                          onClick={() => setRemoveTarget(m)}
                          title="제거"
                        >
                          <TrashIcon width={16} height={16} />
                          제거
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {editOpen && (
        <EditGroupModal
          group={group}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false);
            void load();
          }}
        />
      )}

      {inviteOpen && (
        <InviteMemberModal
          groupId={groupId}
          existingMemberIds={new Set(group.members.map((m) => m.user_id))}
          onClose={() => setInviteOpen(false)}
          onInvited={() => {
            setInviteOpen(false);
            void load();
          }}
        />
      )}

      {/* 소유권 이전 확인 */}
      <Modal
        open={transferTarget !== null}
        title="소유권 이전"
        onClose={() => setTransferTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setTransferTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmTransfer} disabled={busy}>
              {busy ? <Spinner className="h-4 w-4" /> : "소유권 넘기기"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{transferTarget?.display_name}</span>님에게 이 그룹의 소유권을
          넘깁니다. 이전 후 나의 역할은 관리자로 바뀌며, 그룹 삭제·소유권 이전 권한을 잃습니다.
        </p>
      </Modal>

      {/* 멤버 제거 확인 */}
      <Modal
        open={removeTarget !== null}
        title="멤버 제거"
        onClose={() => setRemoveTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setRemoveTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmRemove} disabled={busy}>
              {busy ? <Spinner className="h-4 w-4" /> : "제거"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{removeTarget?.display_name}</span>님을 그룹에서 제거하시겠습니까?
          이 그룹을 통해 공유된 파일에 더 이상 접근할 수 없게 됩니다.
        </p>
      </Modal>

      {/* 그룹 삭제 확인 */}
      <Modal
        open={deleteOpen}
        title="그룹 삭제"
        onClose={() => setDeleteOpen(false)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setDeleteOpen(false)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmDelete} disabled={busy}>
              {busy ? <Spinner className="h-4 w-4" /> : "그룹 삭제"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{group.name}</span> 그룹을 삭제합니다. 이 그룹에 부여된 모든
          파일 권한이 일괄 회수되어 멤버들이 공유 파일에 접근할 수 없게 됩니다. 이 작업은 되돌릴 수
          없습니다.
        </p>
      </Modal>
    </div>
  );
}

// --- 정보 수정 모달 ---------------------------------------------------------

function EditGroupModal({
  group,
  onClose,
  onSaved,
}: {
  group: GroupDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await updateGroup(group.id, {
        name: trimmed,
        description: description.trim() || null,
      });
      toast.success("그룹 정보를 수정했습니다.");
      onSaved();
    } catch (err) {
      const msg =
        errorStatus(err) === 409
          ? "같은 이름의 그룹이 이미 있습니다."
          : extractErrorMessage(err, "수정에 실패했습니다.");
      toast.error(msg);
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title="그룹 정보 수정"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>
            취소
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>
            {busy ? <Spinner className="h-4 w-4" /> : "저장"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor="editName">
            이름
          </label>
          <input
            id="editName"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </div>
        <div>
          <label className="label" htmlFor="editDesc">
            설명
          </label>
          <textarea
            id="editDesc"
            className="input min-h-[80px] resize-none"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>
    </Modal>
  );
}

// --- 멤버 초대 모달 ---------------------------------------------------------

function InviteMemberModal({
  groupId,
  existingMemberIds,
  onClose,
  onInvited,
}: {
  groupId: number;
  existingMemberIds: Set<number>;
  onClose: () => void;
  onInvited: () => void;
}) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserLookup[]>([]);
  // 클릭으로 확정한 초대 대상. null 이면 아직 검색 단계.
  const [selected, setSelected] = useState<UserLookup | null>(null);
  const [role, setRole] = useState<Exclude<GroupRole, "owner">>("member");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 검색어 입력 → 300ms 디바운스 후 이름/이메일 부분 일치 검색. 2자 미만이면 초기화.
  useEffect(() => {
    if (selected) return; // 선택을 확정한 뒤에는 재검색하지 않는다.
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      setSearchError(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    setSearchError(null);
    const handle = setTimeout(async () => {
      try {
        setResults(await searchUsers(trimmed));
      } catch (err) {
        const status = errorStatus(err);
        // 429 는 인터셉터가 Retry-After 토스트로 안내하므로 여기서는 별도 문구만.
        setSearchError(
          status === 429
            ? "검색 요청이 너무 잦습니다. 잠시 후 다시 시도하세요."
            : extractErrorMessage(err, "사용자 검색에 실패했습니다."),
        );
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [query, selected]);

  const invite = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await inviteMember(groupId, selected.id, role);
      toast.success(`${selected.display_name}님을 초대했습니다.`);
      onInvited();
    } catch (err) {
      const status = errorStatus(err);
      if (status === 429) {
        setBusy(false);
        return; // 인터셉터 토스트에 위임.
      }
      const msg =
        status === 404
          ? "해당 사용자를 찾을 수 없습니다."
          : status === 400
            ? "활성 상태의 사용자만 초대할 수 있습니다."
            : status === 409
              ? "이미 이 그룹의 멤버입니다."
              : extractErrorMessage(err, "멤버 초대에 실패했습니다.");
      toast.error(msg);
      setBusy(false);
    }
  };

  // 이미 이 그룹의 멤버인 사용자는 후보에서 제외한다.
  const candidates = results.filter((u) => !existingMemberIds.has(u.id));

  return (
    <Modal
      open
      title="멤버 초대"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>
            취소
          </button>
          <button
            className="btn btn-primary"
            onClick={invite}
            disabled={busy || !selected}
          >
            {busy ? <Spinner className="h-4 w-4" /> : "초대"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {selected ? (
          <div>
            <label className="label">초대할 구성원</label>
            <div className="flex items-center justify-between gap-2 rounded-lg bg-muted-token px-3 py-2">
              <div className="flex min-w-0 flex-col">
                <span className="truncate text-sm font-medium">
                  {selected.display_name}
                </span>
                <span className="truncate text-xs text-muted">{selected.email}</span>
              </div>
              <button
                className="btn btn-ghost shrink-0"
                onClick={() => {
                  setSelected(null);
                  setQuery("");
                }}
              >
                다시 선택
              </button>
            </div>
          </div>
        ) : (
          <div>
            <label className="label" htmlFor="memberSearch">
              구성원 검색
            </label>
            <input
              id="memberSearch"
              className="input"
              type="text"
              placeholder="이름 또는 이메일 (2자 이상)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
            {searchError && (
              <p className="mt-1.5 text-xs" style={{ color: "var(--danger)" }}>
                {searchError}
              </p>
            )}
            <div className="mt-2">
              {searching ? (
                <div className="flex items-center gap-2 px-1 py-3 text-sm text-muted">
                  <Spinner className="h-4 w-4" /> 검색 중…
                </div>
              ) : query.trim().length < 2 ? (
                <p className="px-1 py-3 text-xs text-muted">
                  이름 또는 이메일을 2자 이상 입력하면 구성원이 표시됩니다.
                </p>
              ) : candidates.length === 0 ? (
                <p className="px-1 py-3 text-xs text-muted">
                  {results.length === 0
                    ? "일치하는 구성원이 없습니다."
                    : "일치하는 구성원이 모두 이미 이 그룹의 멤버입니다."}
                </p>
              ) : (
                <ul className="max-h-60 overflow-auto rounded-lg border border-token">
                  {candidates.map((u) => (
                    <li key={u.id}>
                      <button
                        type="button"
                        className="flex w-full flex-col border-b border-token px-3 py-2 text-left last:border-0 hover:bg-[color:var(--bg-muted)]"
                        onClick={() => setSelected(u)}
                      >
                        <span className="truncate text-sm font-medium">
                          {u.display_name}
                        </span>
                        <span className="truncate text-xs text-muted">{u.email}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
        <div>
          <label className="label" htmlFor="inviteRole">
            역할
          </label>
          <select
            id="inviteRole"
            className="input"
            value={role}
            onChange={(e) => setRole(e.target.value as Exclude<GroupRole, "owner">)}
          >
            <option value="member">멤버</option>
            <option value="admin">관리자</option>
          </select>
        </div>
      </div>
    </Modal>
  );
}
