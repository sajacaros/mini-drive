/**
 * 게시판 목록 — **내가 접근 가능한 것만** 보인다.
 *
 * 접근 없는 게시판은 서버가 아예 내려주지 않으므로(이름조차) 여기에 "권한 없음" 같은 상태가
 * 없다. 비어 있으면 그건 권한이 막힌 게 아니라 열린 게시판이 하나도 없다는 뜻이다.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listBoards } from "@/api/boards";
import { extractErrorMessage } from "@/api/client";
import type { Board } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { Badge, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { BoardIcon } from "@/components/icons";

export function BoardsPage() {
  const [boards, setBoards] = useState<Board[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setBoards((await listBoards()).items);
    } catch (err) {
      setError(extractErrorMessage(err, "게시판을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <span className="text-accent">
              <BoardIcon width={18} height={18} />
            </span>
            게시판
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            내가 속한 그룹에 열린 게시판입니다.
          </p>
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
              title="열린 게시판이 없습니다"
              hint="게시판은 관리자가 만들고 그룹에 열어 줍니다."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {boards.map((b) => (
                <li key={b.id}>
                  <Link
                    to={`/boards/${b.id}`}
                    className="card flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[color:var(--bg-muted)]"
                  >
                    <span className="text-muted">
                      <BoardIcon width={18} height={18} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-medium">{b.name}</span>
                        <BoardPermissionBadge permission={b.permission} />
                      </div>
                      {b.description && (
                        <p className="mt-0.5 truncate text-sm text-muted">{b.description}</p>
                      )}
                    </div>
                    <span className="shrink-0 text-sm tabular-nums text-muted">
                      글 {b.post_count}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * 내 권한 배지. `null` 은 그룹 할당 없이 관리자 자격으로 열람 중이라는 뜻이라 "열람"으로 쓴다 —
 * 그 상태에서는 글도 댓글도 쓸 수 없다.
 */
export function BoardPermissionBadge({
  permission,
}: {
  permission: "read" | "write" | null;
}) {
  if (permission === "write") return <Badge tone="success">쓰기</Badge>;
  if (permission === "read") return <Badge>읽기</Badge>;
  return <Badge tone="warning">관리자 열람</Badge>;
}
