/**
 * 위키 스페이스 목록 (PRD 6.8, Phase 7-3). 스페이스 카드 + 생성/삭제.
 *
 * 위키 페이지 자체는 드라이브 파일이므로, 스페이스를 지워도 페이지 폴더는 드라이브에 남는다
 * (삭제 확인 모달에서 안내). group 스코프 생성은 그룹 admin 이상만 가능하다(백엔드 403).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import { listGroups } from "@/api/groups";
import type { GroupSummary } from "@/api/types";
import {
  type WikiScope,
  type WikiSpace,
  createSpace,
  deleteSpace,
  listSpaces,
} from "@/api/wiki";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { BookIcon, PlusIcon, TrashIcon } from "@/components/icons";
import { formatDate } from "@/lib/format";

export function WikiPage() {
  const toast = useToast();
  const navigate = useNavigate();

  const [spaces, setSpaces] = useState<WikiSpace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 그룹 스코프 생성용 — 내가 admin 이상인 그룹만 선택지로 노출한다.
  const [manageableGroups, setManageableGroups] = useState<GroupSummary[]>([]);

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<WikiScope>("personal");
  const [groupId, setGroupId] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<WikiSpace | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSpaces(await listSpaces());
    } catch (err) {
      setError(extractErrorMessage(err, "위키 스페이스를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = async () => {
    setName("");
    setScope("personal");
    setGroupId("");
    setCreateOpen(true);
    // 그룹 스코프 선택지 준비(내가 owner/admin 인 그룹만).
    try {
      const res = await listGroups(1, 100);
      setManageableGroups(
        res.items.filter((g) => g.my_role === "owner" || g.my_role === "admin"),
      );
    } catch {
      setManageableGroups([]);
    }
  };

  const submitCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (scope === "group" && groupId === "") {
      toast.error("그룹을 선택하세요.");
      return;
    }
    setSubmitting(true);
    try {
      const space = await createSpace({
        name: trimmed,
        scope,
        group_id: scope === "group" ? Number(groupId) : null,
      });
      toast.success("위키 스페이스를 만들었습니다.");
      setCreateOpen(false);
      navigate(`/wiki/${space.id}`);
    } catch (err) {
      const msg =
        errorStatus(err) === 403
          ? "그룹 admin 이상만 그룹 위키를 만들 수 있습니다."
          : extractErrorMessage(err, "스페이스 생성에 실패했습니다.");
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteSpace(deleteTarget.id);
      setSpaces((prev) => prev.filter((s) => s.id !== deleteTarget.id));
      setDeleteTarget(null);
      toast.success("위키 스페이스를 삭제했습니다.");
    } catch (err) {
      toast.error(extractErrorMessage(err, "스페이스 삭제에 실패했습니다."));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <div className="flex items-center justify-between gap-4 border-b border-token px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">위키</h1>
          <p className="mt-0.5 text-sm text-muted">
            드라이브 문서를 근거로 LLM 이 정리한 위키 페이지를 스페이스 단위로 관리합니다.
          </p>
        </div>
        <button className="btn btn-primary shrink-0" onClick={() => void openCreate()}>
          <PlusIcon width={16} height={16} />
          스페이스 만들기
        </button>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : spaces.length === 0 ? (
          <EmptyState
            icon={<BookIcon width={40} height={40} />}
            title="아직 위키 스페이스가 없습니다"
            hint="스페이스를 만들고 소스 문서를 등록하면 위키 페이지가 자동으로 정리됩니다."
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {spaces.map((s) => (
              <div
                key={s.id}
                className="group card flex flex-col gap-2 p-4 transition-shadow hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-2">
                  <button
                    className="min-w-0 flex-1 truncate text-left font-medium"
                    onClick={() => navigate(`/wiki/${s.id}`)}
                    title={s.name}
                  >
                    {s.name}
                  </button>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Badge tone={s.scope === "group" ? "warning" : "neutral"}>
                      {s.scope === "group" ? "그룹" : "개인"}
                    </Badge>
                    <button
                      className="btn btn-ghost px-1.5 py-1 text-muted opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"
                      onClick={() => setDeleteTarget(s)}
                      aria-label="스페이스 삭제"
                      title="스페이스 삭제"
                    >
                      <TrashIcon width={14} height={14} />
                    </button>
                  </div>
                </div>
                <button
                  className="text-left text-xs text-muted"
                  onClick={() => navigate(`/wiki/${s.id}`)}
                >
                  {formatDate(s.created_at)} 생성
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 생성 모달 */}
      <Modal
        open={createOpen}
        title="위키 스페이스 만들기"
        onClose={() => setCreateOpen(false)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setCreateOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitCreate} disabled={submitting}>
              {submitting ? <Spinner className="h-4 w-4" /> : "만들기"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <div>
            <label className="label" htmlFor="wikiName">
              이름
            </label>
            <input
              id="wikiName"
              className="input"
              placeholder="예: 제품 문서 위키"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitCreate()}
              autoFocus
            />
          </div>
          <div>
            <label className="label">범위</label>
            <div className="flex gap-2">
              <ScopeButton
                active={scope === "personal"}
                label="개인"
                hint="나만"
                onClick={() => setScope("personal")}
              />
              <ScopeButton
                active={scope === "group"}
                label="그룹"
                hint="그룹원 공유"
                onClick={() => setScope("group")}
              />
            </div>
          </div>
          {scope === "group" && (
            <div>
              <label className="label" htmlFor="wikiGroup">
                그룹
              </label>
              {manageableGroups.length === 0 ? (
                <p className="text-sm text-muted">
                  admin 이상 권한이 있는 그룹이 없습니다. 그룹 위키는 그룹 관리자만 만들 수 있습니다.
                </p>
              ) : (
                <select
                  id="wikiGroup"
                  className="input"
                  value={groupId}
                  onChange={(e) => setGroupId(e.target.value === "" ? "" : Number(e.target.value))}
                >
                  <option value="">그룹 선택...</option>
                  {manageableGroups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
        </div>
      </Modal>

      {/* 삭제 확인 */}
      <Modal
        open={deleteTarget !== null}
        title="위키 스페이스 삭제"
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmDelete} disabled={deleting}>
              {deleting ? <Spinner className="h-4 w-4" /> : "삭제"}
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{deleteTarget?.name}</span> 스페이스를 삭제할까요? 소스
          등록과 잡 이력이 사라집니다.
        </p>
        <p className="mt-2 text-sm text-muted">
          이미 만들어진 위키 페이지 폴더는 드라이브에 그대로 남습니다(별도로 삭제해야 합니다).
        </p>
      </Modal>
    </div>
  );
}

function ScopeButton({
  active,
  label,
  hint,
  onClick,
}: {
  active: boolean;
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-1 flex-col items-start rounded-lg border px-3 py-2 text-left transition-colors ${
        active
          ? "border-[color:var(--accent)] bg-[color:var(--bg-muted)]"
          : "border-token hover:bg-[color:var(--bg-muted)]"
      }`}
    >
      <span className={`text-sm font-medium ${active ? "text-[color:var(--accent)]" : ""}`}>
        {label}
      </span>
      <span className="text-xs text-muted">{hint}</span>
    </button>
  );
}
