/**
 * 게시판 하나 — 글 목록(최신순) + 글쓰기.
 *
 * 글쓰기 버튼은 `permission === "write"` 일 때만 나온다. 관리자라도 그룹 write 가 없으면
 * 서버가 403 을 주므로 버튼을 감추는 것이 화면과 서버의 답을 일치시키는 유일한 방법이다.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { createPost, getBoard, listPosts } from "@/api/boards";
import { extractErrorMessage } from "@/api/client";
import type { Board, BoardPostSummary } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Pagination,
} from "@/components/ui";
import { BoardIcon, ChevronLeftIcon, PlusIcon } from "@/components/icons";
import { formatDateTime } from "@/lib/format";
import { BoardPermissionBadge } from "./BoardsPage";

const PAGE_SIZE = 20;

export function BoardPage() {
  const { boardId } = useParams<{ boardId: string }>();
  const id = Number(boardId);
  const navigate = useNavigate();
  const toast = useToast();

  const [board, setBoard] = useState<Board | null>(null);
  const [posts, setPosts] = useState<BoardPostSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 글쓰기 — 평문 textarea + 미리보기 탭. 마크다운 렌더러가 이미 있어 새 의존성이 없다.
  const [composing, setComposing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [b, list] = await Promise.all([getBoard(id), listPosts(id, page, PAGE_SIZE)]);
      setBoard(b);
      setPosts(list.items);
      setTotal(list.total);
    } catch (err) {
      setError(extractErrorMessage(err, "게시판을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [id, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    const clean = title.trim();
    if (!clean) {
      toast.error("제목을 입력하세요.");
      return;
    }
    setSaving(true);
    try {
      const post = await createPost(id, clean, body);
      toast.success("글을 올렸습니다.");
      setComposing(false);
      setTitle("");
      setBody("");
      setPreview(false);
      navigate(`/boards/${id}/posts/${post.id}`);
    } catch (err) {
      toast.error(extractErrorMessage(err, "글을 올리지 못했습니다."));
    } finally {
      setSaving(false);
    }
  };

  const canWrite = board?.permission === "write";

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <Link
                to="/boards"
                className="mb-1 inline-flex items-center gap-1 text-sm text-muted hover:text-[color:var(--text-primary)]"
              >
                <ChevronLeftIcon width={14} height={14} />
                게시판 목록
              </Link>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                <span className="text-accent">
                  <BoardIcon width={18} height={18} />
                </span>
                <span className="truncate">{board?.name ?? "게시판"}</span>
                {board && <BoardPermissionBadge permission={board.permission} />}
              </h1>
              {board?.description && (
                <p className="mt-0.5 text-sm text-muted">{board.description}</p>
              )}
            </div>
            {canWrite && !composing && (
              <button className="btn btn-primary" onClick={() => setComposing(true)}>
                <PlusIcon width={16} height={16} />
                글쓰기
              </button>
            )}
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto w-full max-w-3xl">
          {composing && (
            <div className="card mb-4 flex flex-col gap-3 p-4">
              <input
                className="input"
                placeholder="제목"
                value={title}
                maxLength={500}
                onChange={(e) => setTitle(e.target.value)}
              />
              <div className="flex gap-2 text-sm">
                <button
                  type="button"
                  className={`btn btn-ghost ${preview ? "" : "text-accent"}`}
                  onClick={() => setPreview(false)}
                >
                  작성
                </button>
                <button
                  type="button"
                  className={`btn btn-ghost ${preview ? "text-accent" : ""}`}
                  onClick={() => setPreview(true)}
                >
                  미리보기
                </button>
              </div>
              {preview ? (
                <div className="min-h-40 rounded-lg border border-token p-3">
                  {body.trim() ? (
                    <Markdown text={body} />
                  ) : (
                    <p className="text-sm text-muted">내용이 비어 있습니다.</p>
                  )}
                </div>
              ) : (
                <textarea
                  className="input min-h-40 font-mono text-sm"
                  placeholder="본문 (마크다운). 큰 파일은 드라이브에 올리고 공유 링크를 붙이세요."
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              )}
              <div className="flex justify-end gap-2">
                <button
                  className="btn btn-secondary"
                  onClick={() => {
                    setComposing(false);
                    setPreview(false);
                  }}
                  disabled={saving}
                >
                  취소
                </button>
                <button className="btn btn-primary" onClick={() => void submit()} disabled={saving}>
                  {saving ? "올리는 중..." : "올리기"}
                </button>
              </div>
            </div>
          )}

          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void load()} />
          ) : posts.length === 0 ? (
            <EmptyState
              icon={<BoardIcon width={40} height={40} />}
              title="아직 글이 없습니다"
              hint={canWrite ? "'글쓰기'로 첫 글을 남겨 보세요." : undefined}
            />
          ) : (
            <>
              <ul className="flex flex-col gap-2">
                {posts.map((p) => (
                  <li key={p.id}>
                    <Link
                      to={`/boards/${id}/posts/${p.id}`}
                      className="card flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[color:var(--bg-muted)]"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-medium">{p.title}</span>
                          {p.comment_count > 0 && (
                            <span className="shrink-0 text-sm tabular-nums text-accent">
                              [{p.comment_count}]
                            </span>
                          )}
                          {p.attachment_count > 0 && (
                            <span className="shrink-0 text-xs text-muted">
                              첨부 {p.attachment_count}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 text-sm text-muted">
                          {p.author_name} · {formatDateTime(p.created_at)}
                        </p>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
              <Pagination
                page={page}
                totalPages={Math.max(1, Math.ceil(total / PAGE_SIZE))}
                onChange={setPage}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
