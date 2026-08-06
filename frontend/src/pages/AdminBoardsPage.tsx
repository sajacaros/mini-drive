/**
 * 게시판 관리 (관리자) — 생성·이름 수정·삭제 + 그룹 할당.
 *
 * 게시판별 운영자 역할이 없으므로 이 화면이 게시판 운영의 전부다. 그룹을 붙이는 것이 곧
 * "게시판을 여는" 행위이고, 떼는 것이 곧 닫는 행위다 — 글은 남고 접근만 끊긴다.
 */

import { useCallback, useEffect, useState } from "react";

import {
  adminCreateBoard,
  adminDeleteBoard,
  adminGrantBoardGroup,
  adminListBoardGroups,
  adminListBoards,
  adminRevokeBoardGroup,
  adminUpdateBoard,
} from "@/api/boards";
import { extractErrorMessage } from "@/api/client";
import { listGroups } from "@/api/groups";
import type { AdminBoard, BoardGroupGrant, BoardPermission, Group } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { BoardIcon, PlusIcon, RenameIcon, TrashIcon, UsersIcon } from "@/components/icons";
import { useBoardAccessStore } from "@/store/boardAccess";

export function AdminBoardsPage() {
  const toast = useToast();

  const [boards, setBoards] = useState<AdminBoard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 생성/이름 수정 모달 — id 가 null 이면 새로 만들기.
  const [edit, setEdit] = useState<{ id: number | null; name: string; description: string } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);

  // 그룹 할당 모달.
  const [groupTarget, setGroupTarget] = useState<AdminBoard | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setBoards((await adminListBoards()).items);
    } catch (err) {
      setError(extractErrorMessage(err, "게시판을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!edit) return;
    const name = edit.name.trim();
    if (!name) {
      toast.error("게시판 이름을 입력하세요.");
      return;
    }
    setSaving(true);
    try {
      if (edit.id === null) {
        await adminCreateBoard(name, edit.description.trim() || null);
        toast.success("게시판을 만들었습니다.");
      } else {
        await adminUpdateBoard(edit.id, {
          name,
          description: edit.description.trim() || null,
        });
        toast.success("게시판을 수정했습니다.");
      }
      setEdit(null);
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "저장에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (b: AdminBoard) => {
    if (
      !window.confirm(
        `게시판 "${b.name}"을(를) 삭제할까요?\n` +
          `글 ${b.post_count}건이 잠기고 첨부 파일은 즉시 삭제되어 되살릴 수 없습니다.`,
      )
    )
      return;
    try {
      await adminDeleteBoard(b.id);
      toast.success("게시판을 삭제했습니다.");
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "삭제에 실패했습니다."));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                <span className="text-accent">
                  <BoardIcon width={18} height={18} />
                </span>
                게시판 관리
              </h1>
              <p className="mt-0.5 text-sm text-muted">
                게시판을 만들고 그룹을 할당해 엽니다. 할당된 그룹이 없으면 아무에게도 보이지
                않습니다.
              </p>
            </div>
            <button
              className="btn btn-primary"
              onClick={() => setEdit({ id: null, name: "", description: "" })}
            >
              <PlusIcon width={16} height={16} />
              게시판 추가
            </button>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto w-full max-w-3xl">
          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void load()} />
          ) : boards.length === 0 ? (
            <EmptyState
              icon={<BoardIcon width={40} height={40} />}
              title="게시판이 없습니다"
              hint="'게시판 추가'로 첫 게시판을 만들고 그룹을 할당하세요."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {boards.map((b) => (
                <li key={b.id} className="card flex items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{b.name}</span>
                      {b.group_count === 0 && <Badge tone="warning">그룹 미할당</Badge>}
                    </div>
                    <p className="mt-0.5 text-sm text-muted">
                      {b.description ? `${b.description} · ` : ""}
                      그룹 {b.group_count} · 글 {b.post_count}
                    </p>
                  </div>
                  <button
                    className="btn btn-secondary"
                    onClick={() => setGroupTarget(b)}
                    title="그룹 할당"
                  >
                    <UsersIcon width={16} height={16} />
                    그룹
                  </button>
                  <button
                    className="btn btn-ghost p-1.5"
                    title="이름·설명 수정"
                    onClick={() =>
                      setEdit({ id: b.id, name: b.name, description: b.description ?? "" })
                    }
                  >
                    <RenameIcon width={16} height={16} />
                  </button>
                  <button
                    className="btn btn-ghost p-1.5 text-[color:var(--danger)]"
                    title="게시판 삭제"
                    onClick={() => void remove(b)}
                  >
                    <TrashIcon width={16} height={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <Modal
        open={edit !== null}
        title={edit?.id === null ? "게시판 추가" : "게시판 수정"}
        onClose={() => setEdit(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setEdit(null)} disabled={saving}>
              취소
            </button>
            <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
              {saving ? "저장 중..." : "저장"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">이름</span>
            <input
              className="input"
              value={edit?.name ?? ""}
              maxLength={100}
              onChange={(e) => setEdit((s) => (s ? { ...s, name: e.target.value } : s))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">설명 (선택)</span>
            <input
              className="input"
              value={edit?.description ?? ""}
              maxLength={2000}
              onChange={(e) => setEdit((s) => (s ? { ...s, description: e.target.value } : s))}
            />
          </label>
        </div>
      </Modal>

      {groupTarget && (
        <BoardGroupsModal
          board={groupTarget}
          onClose={() => {
            setGroupTarget(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

/**
 * 게시판 ↔ 그룹 할당 모달. 같은 그룹을 다시 할당하면 권한이 갈아끼워진다(멱등 upsert)라
 * "이미 있음" 같은 오류 상태를 화면에 둘 필요가 없다.
 */
function BoardGroupsModal({ board, onClose }: { board: AdminBoard; onClose: () => void }) {
  const toast = useToast();
  // 할당을 바꾸면 사이드바의 "게시판" 노출 조건도 달라진다 — 캐시를 무르게 해 다음 조회가
  // 서버를 다시 읽게 한다(관리자 자신이 그 게시판에 속하게 된 경우가 흔하다).
  const invalidateBoardAccess = useBoardAccessStore((s) => s.invalidate);
  const [grants, setGrants] = useState<BoardGroupGrant[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [pickGroup, setPickGroup] = useState<number | "">("");
  const [pickPermission, setPickPermission] = useState<BoardPermission>("read");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [g, all] = await Promise.all([
        adminListBoardGroups(board.id),
        listGroups(1, 100),
      ]);
      setGrants(g.items);
      setGroups(all.items);
    } catch (err) {
      toast.error(extractErrorMessage(err, "그룹 정보를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [board.id, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const grant = async () => {
    if (pickGroup === "") return;
    setBusy(true);
    try {
      await adminGrantBoardGroup(board.id, Number(pickGroup), pickPermission);
      setPickGroup("");
      invalidateBoardAccess();
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "그룹 할당에 실패했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (g: BoardGroupGrant) => {
    if (
      !window.confirm(
        `"${g.group_name}"의 접근을 회수할까요?\n` +
          "이 그룹으로만 접근하던 사람은 자기가 쓴 글도 볼 수 없게 됩니다(글은 남습니다).",
      )
    )
      return;
    setBusy(true);
    try {
      await adminRevokeBoardGroup(board.id, g.group_id);
      invalidateBoardAccess();
      await load();
    } catch (err) {
      toast.error(extractErrorMessage(err, "회수에 실패했습니다."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title={`"${board.name}" 그룹 할당`}
      onClose={onClose}
      footer={
        <button className="btn btn-secondary" onClick={onClose}>
          닫기
        </button>
      }
    >
      {loading ? (
        <LoadingState />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex min-w-40 flex-1 flex-col gap-1 text-sm">
              <span className="text-muted">그룹</span>
              <select
                className="input"
                value={pickGroup}
                onChange={(e) => setPickGroup(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">선택하세요</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-muted">권한</span>
              <select
                className="input"
                value={pickPermission}
                onChange={(e) => setPickPermission(e.target.value as BoardPermission)}
              >
                <option value="read">읽기</option>
                <option value="write">쓰기</option>
              </select>
            </label>
            <button
              className="btn btn-primary"
              onClick={() => void grant()}
              disabled={busy || pickGroup === ""}
            >
              할당
            </button>
          </div>

          {grants.length === 0 ? (
            <p className="text-sm text-muted">
              할당된 그룹이 없습니다. 이 게시판은 지금 아무에게도 보이지 않습니다.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {grants.map((g) => (
                <li
                  key={g.group_id}
                  className="flex items-center gap-2 rounded-lg border border-token px-3 py-2 text-sm"
                >
                  <span className="min-w-0 flex-1 truncate">{g.group_name}</span>
                  <Badge tone={g.permission === "write" ? "success" : "neutral"}>
                    {g.permission === "write" ? "쓰기" : "읽기"}
                  </Badge>
                  <button
                    className="btn btn-ghost p-1.5 text-[color:var(--danger)]"
                    title="회수"
                    onClick={() => void revoke(g)}
                    disabled={busy}
                  >
                    <TrashIcon width={16} height={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Modal>
  );
}
