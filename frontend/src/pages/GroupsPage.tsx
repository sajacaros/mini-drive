import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import { createGroup, listGroups } from "@/api/groups";
import type { GroupSummary } from "@/api/types";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { PlusIcon, UserIcon, UsersIcon } from "@/components/icons";
import { formatDate } from "@/lib/format";
import { roleLabel, roleTone } from "@/lib/labels";

const PAGE_SIZE = 50;

export function GroupsPage() {
  const toast = useToast();
  const navigate = useNavigate();

  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async (pageNum: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listGroups(pageNum, PAGE_SIZE);
      setGroups(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "그룹 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(page);
  }, [page, load]);

  const submitCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSubmitting(true);
    try {
      const group = await createGroup(trimmed, description.trim() || undefined);
      toast.success("그룹을 만들었습니다.");
      setCreateOpen(false);
      setName("");
      setDescription("");
      navigate(`/groups/${group.id}`);
    } catch (err) {
      const msg =
        errorStatus(err) === 409
          ? "같은 이름의 그룹이 이미 있습니다."
          : extractErrorMessage(err, "그룹 생성에 실패했습니다.");
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">그룹</h1>
          <p className="mt-0.5 text-sm text-muted">파일 공유의 단위가 되는 그룹을 관리합니다.</p>
        </div>
        <button className="btn btn-primary shrink-0" onClick={() => setCreateOpen(true)}>
          <PlusIcon width={16} height={16} />
          그룹 만들기
        </button>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={() => load(page)} />
        ) : groups.length === 0 ? (
          <EmptyState
            icon={<UsersIcon width={40} height={40} />}
            title="아직 그룹이 없습니다"
            hint="그룹을 만들어 파일을 함께 공유하세요."
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {groups.map((g) => (
              <button
                key={g.id}
                className="card flex flex-col gap-2 p-4 text-left transition-shadow hover:shadow-md"
                onClick={() => navigate(`/groups/${g.id}`)}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="truncate font-medium">{g.name}</span>
                  {g.my_role && <Badge tone={roleTone(g.my_role)}>{roleLabel(g.my_role)}</Badge>}
                </div>
                <p className="line-clamp-2 min-h-[2.5rem] text-sm text-muted">
                  {g.description || "설명 없음"}
                </p>
                <div className="flex items-center gap-1.5 text-xs text-muted">
                  <UserIcon width={14} height={14} className="shrink-0" />
                  <span className="truncate">소유자 {g.owner_display_name}</span>
                </div>
                <div className="flex items-center justify-between text-xs text-muted">
                  <span>멤버 {g.member_count}명</span>
                  <span>{formatDate(g.created_at)} 생성</span>
                </div>
              </button>
            ))}
          </div>
        )}

        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-center gap-3 text-sm">
            <button
              className="btn btn-secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              이전
            </button>
            <span className="text-muted">
              {page} / {totalPages}
            </span>
            <button
              className="btn btn-secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </button>
          </div>
        )}
      </div>

      <Modal
        open={createOpen}
        title="그룹 만들기"
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
            <label className="label" htmlFor="groupName">
              이름
            </label>
            <input
              id="groupName"
              className="input"
              placeholder="예: 디자인팀"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitCreate()}
              autoFocus
            />
          </div>
          <div>
            <label className="label" htmlFor="groupDesc">
              설명 (선택)
            </label>
            <textarea
              id="groupDesc"
              className="input min-h-[80px] resize-none"
              placeholder="그룹의 용도를 적어 두면 좋아요."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
