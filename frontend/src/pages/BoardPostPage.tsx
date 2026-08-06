/**
 * 글 상세 — 본문(마크다운) + 첨부 + 댓글.
 *
 * 버튼 노출은 전부 서버가 내려준 `can_edit` / `can_delete` / `can_delete`(댓글)를 따른다.
 * 프런트가 "본인인가?"를 다시 계산하지 않는 이유는 백엔드와 같다 — 판정을 두 곳에 두면 한쪽이
 * 어긋났을 때 화면과 서버의 답이 갈린다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  createComment,
  deleteAttachment,
  deleteComment,
  deletePost,
  downloadAttachment,
  getBoard,
  getPost,
  listComments,
  updatePost,
  uploadAttachment,
} from "@/api/boards";
import { extractErrorMessage } from "@/api/client";
import type { Board, BoardComment, BoardPostDetail } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { ErrorState, LoadingState } from "@/components/ui";
import {
  ChevronLeftIcon,
  DownloadIcon,
  RenameIcon,
  TrashIcon,
  UploadIcon,
} from "@/components/icons";
import { formatBytes, formatDateTime } from "@/lib/format";

export function BoardPostPage() {
  const { boardId, postId } = useParams<{ boardId: string; postId: string }>();
  const bid = Number(boardId);
  const pid = Number(postId);
  const navigate = useNavigate();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);

  const [board, setBoard] = useState<Board | null>(null);
  const [post, setPost] = useState<BoardPostDetail | null>(null);
  const [comments, setComments] = useState<BoardComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);

  const [commentBody, setCommentBody] = useState("");
  const [posting, setPosting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [b, p, cs] = await Promise.all([
        getBoard(bid),
        getPost(bid, pid),
        listComments(bid, pid),
      ]);
      setBoard(b);
      setPost(p);
      setComments(cs.items);
    } catch (err) {
      setError(extractErrorMessage(err, "글을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [bid, pid]);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = () => {
    if (!post) return;
    setTitle(post.title);
    setBody(post.body);
    setPreview(false);
    setEditing(true);
  };

  const saveEdit = async () => {
    const clean = title.trim();
    if (!clean) {
      toast.error("제목을 입력하세요.");
      return;
    }
    setSaving(true);
    try {
      setPost(await updatePost(bid, pid, { title: clean, body }));
      setEditing(false);
      toast.success("글을 수정했습니다.");
    } catch (err) {
      toast.error(extractErrorMessage(err, "수정에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (
      !window.confirm(
        "이 글을 삭제할까요?\n첨부 파일은 즉시 삭제되며 되살릴 수 없습니다.",
      )
    )
      return;
    try {
      await deletePost(bid, pid);
      toast.success("글을 삭제했습니다.");
      navigate(`/boards/${bid}`);
    } catch (err) {
      toast.error(extractErrorMessage(err, "삭제에 실패했습니다."));
    }
  };

  const upload = async (file: File) => {
    try {
      await uploadAttachment(bid, pid, file);
      setPost(await getPost(bid, pid));
      toast.success("첨부를 올렸습니다.");
    } catch (err) {
      toast.error(extractErrorMessage(err, "첨부 업로드에 실패했습니다."));
    }
  };

  const removeAttachment = async (attachmentId: number, filename: string) => {
    if (!window.confirm(`첨부 "${filename}"을(를) 삭제할까요?\n되살릴 수 없습니다.`)) return;
    try {
      await deleteAttachment(bid, pid, attachmentId);
      setPost(await getPost(bid, pid));
    } catch (err) {
      toast.error(extractErrorMessage(err, "첨부 삭제에 실패했습니다."));
    }
  };

  const submitComment = async () => {
    const clean = commentBody.trim();
    if (!clean) return;
    setPosting(true);
    try {
      const created = await createComment(bid, pid, clean);
      setComments((list) => [...list, created]);
      setCommentBody("");
    } catch (err) {
      toast.error(extractErrorMessage(err, "댓글을 남기지 못했습니다."));
    } finally {
      setPosting(false);
    }
  };

  const removeComment = async (commentId: number) => {
    if (!window.confirm("이 댓글을 삭제할까요?")) return;
    try {
      await deleteComment(bid, pid, commentId);
      setComments((await listComments(bid, pid)).items);
    } catch (err) {
      toast.error(extractErrorMessage(err, "댓글 삭제에 실패했습니다."));
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <LoadingState />
      </div>
    );
  }
  if (error || !post) {
    return (
      <div className="p-6">
        <ErrorState
          message={error ?? "글을 찾을 수 없습니다."}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  const canWrite = board?.permission === "write";
  // 첨부는 작성자만 붙인다 — 수정 권한과 같은 조건이라 can_edit 을 그대로 쓴다.
  const canAttach = canWrite && post.can_edit;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <Link
            to={`/boards/${bid}`}
            className="mb-1 inline-flex items-center gap-1 text-sm text-muted hover:text-[color:var(--text-primary)]"
          >
            <ChevronLeftIcon width={14} height={14} />
            {board?.name ?? "게시판"}
          </Link>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold">{post.title}</h1>
              <p className="mt-0.5 text-sm text-muted">
                {post.author_name} · {formatDateTime(post.created_at)}
                {post.updated_at !== post.created_at && " (수정됨)"}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              {post.can_edit && !editing && (
                <button className="btn btn-secondary" onClick={startEdit}>
                  <RenameIcon width={16} height={16} />
                  수정
                </button>
              )}
              {post.can_delete && (
                <button className="btn btn-danger" onClick={() => void remove()}>
                  <TrashIcon width={16} height={16} />
                  삭제
                </button>
              )}
            </div>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
          {/* 본문 */}
          {editing ? (
            <div className="card flex flex-col gap-3 p-4">
              <input
                className="input"
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
                  <Markdown text={body} />
                </div>
              ) : (
                <textarea
                  className="input min-h-40 font-mono text-sm"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              )}
              <div className="flex justify-end gap-2">
                <button
                  className="btn btn-secondary"
                  onClick={() => setEditing(false)}
                  disabled={saving}
                >
                  취소
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => void saveEdit()}
                  disabled={saving}
                >
                  {saving ? "저장 중..." : "저장"}
                </button>
              </div>
            </div>
          ) : (
            <div className="card p-4">
              {post.body.trim() ? (
                <Markdown text={post.body} />
              ) : (
                <p className="text-sm text-muted">본문이 없습니다.</p>
              )}
            </div>
          )}

          {/* 첨부 */}
          {(post.attachments.length > 0 || canAttach) && (
            <section>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-muted">
                  첨부 {post.attachments.length > 0 && `(${post.attachments.length})`}
                </h2>
                {canAttach && (
                  <>
                    <button
                      className="btn btn-secondary"
                      onClick={() => fileInput.current?.click()}
                    >
                      <UploadIcon width={16} height={16} />
                      파일 추가
                    </button>
                    <input
                      ref={fileInput}
                      type="file"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (f) void upload(f);
                      }}
                    />
                  </>
                )}
              </div>
              {post.attachments.length === 0 ? (
                <p className="text-sm text-muted">
                  첨부가 없습니다. 큰 파일은 드라이브에 올리고 공유 링크를 본문에 붙이세요.
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {post.attachments.map((a) => (
                    <li
                      key={a.id}
                      className="card flex items-center gap-3 px-3 py-2 text-sm"
                    >
                      <span className="min-w-0 flex-1 truncate">{a.filename}</span>
                      <span className="shrink-0 tabular-nums text-muted">
                        {formatBytes(a.size)}
                      </span>
                      <button
                        className="btn btn-ghost p-1.5"
                        title="다운로드"
                        onClick={() => void downloadAttachment(bid, pid, a)}
                      >
                        <DownloadIcon width={16} height={16} />
                      </button>
                      {post.can_delete && (
                        <button
                          className="btn btn-ghost p-1.5 text-[color:var(--danger)]"
                          title="첨부 삭제"
                          onClick={() => void removeAttachment(a.id, a.filename)}
                        >
                          <TrashIcon width={16} height={16} />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {/* 댓글 */}
          <section>
            <h2 className="mb-2 text-sm font-semibold text-muted">
              댓글 {comments.length > 0 && `(${comments.length})`}
            </h2>
            {comments.length === 0 ? (
              <p className="text-sm text-muted">아직 댓글이 없습니다.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {comments.map((c) => (
                  <li key={c.id} className={`card px-3 py-2 ${c.is_deleted ? "opacity-60" : ""}`}>
                    <div className="flex items-center gap-2 text-xs text-muted">
                      <span className="font-medium text-[color:var(--text-primary)]">
                        {c.author_name}
                      </span>
                      <span>{formatDateTime(c.created_at)}</span>
                      {c.can_delete && (
                        <button
                          className="btn btn-ghost ml-auto p-1"
                          title="댓글 삭제"
                          onClick={() => void removeComment(c.id)}
                        >
                          <TrashIcon width={14} height={14} />
                        </button>
                      )}
                    </div>
                    <p
                      className={`mt-1 whitespace-pre-wrap text-sm ${
                        c.is_deleted ? "italic text-muted" : ""
                      }`}
                    >
                      {c.body}
                    </p>
                  </li>
                ))}
              </ul>
            )}

            {canWrite && (
              <div className="mt-3 flex flex-col gap-2">
                <textarea
                  className="input min-h-20 text-sm"
                  placeholder="댓글을 남기세요. (수정은 없습니다 — 지우고 다시 씁니다)"
                  value={commentBody}
                  maxLength={5000}
                  onChange={(e) => setCommentBody(e.target.value)}
                />
                <div className="flex justify-end">
                  <button
                    className="btn btn-primary"
                    onClick={() => void submitComment()}
                    disabled={posting || !commentBody.trim()}
                  >
                    {posting ? "등록 중..." : "댓글 등록"}
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
