/**
 * 챗봇 페이지 (PRD 6.9, Phase 7-2). 좌측 세션 목록 + 우측 대화창.
 *
 * 질문 전송은 SSE 스트림이라(api/chat.ts) 토큰을 실시간으로 assistant 말풍선에 누적한다.
 * 세션 미선택 상태에서 첫 전송이면 세션을 자동 생성한 뒤 진행한다. citations 이벤트는 말풍선
 * 하단의 출처 칩으로, 클릭 시 미리보기 모달(권한 회수 파일은 정중한 오류)을 연다.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { errorStatus, extractErrorMessage } from "@/api/client";
import {
  type ChatCitation,
  type ChatSessionSummary,
  createSession,
  deleteSession,
  getSession,
  listSessions,
  streamMessage,
} from "@/api/chat";
import { promoteAnswer } from "@/api/wiki";
import { Markdown } from "@/components/Markdown";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { PreviewModal } from "@/components/PreviewModal";
import { Badge, EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui";
import {
  BookIcon,
  ChatIcon,
  FileIcon,
  PlusIcon,
  SendIcon,
  StopIcon,
  TrashIcon,
} from "@/components/icons";
import { downloadFile } from "@/lib/download";
import { formatDate } from "@/lib/format";
import { fetchFilePreview } from "@/lib/preview";

/** 화면에 그리는 메시지 — 서버 메시지(id: number)와 스트리밍 임시 메시지(id: string)를 겸한다. */
interface DisplayMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  citations: ChatCitation[] | null;
  streaming?: boolean;
  error?: string;
}

let tempSeq = 0;
const nextTempId = () => `tmp-${++tempSeq}`;

export function ChatPage() {
  const toast = useToast();

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<ChatSessionSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [preview, setPreview] = useState<{ fileId: number; name: string } | null>(null);

  // 새 대화(activeId==null)에 적용할 검색 범위. true = 전사 위키 범위, false = 전체 범위.
  const [newWikiScope, setNewWikiScope] = useState(false);

  // 답변 승격 모달 (전사 위키 단일 — 대상 선택 없음).
  const [promoteTarget, setPromoteTarget] = useState<DisplayMessage | null>(null);
  const [promoteTitle, setPromoteTitle] = useState("");
  const [promoting, setPromoting] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  // 오류 후 재시도용 — 마지막으로 스트리밍한 (세션, 질문).
  const pendingRef = useRef<{ sessionId: number; question: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      setSessions(await listSessions());
    } catch (err) {
      setSessionsError(extractErrorMessage(err, "세션 목록을 불러오지 못했습니다."));
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // 활성 세션 변경 시 히스토리 로드. 스트리밍 중 전환은 아래 selectSession 이 막는다.
  const openSession = useCallback(
    async (id: number) => {
      setActiveId(id);
      setMessagesLoading(true);
      setMessages([]);
      try {
        const detail = await getSession(id);
        setMessages(
          detail.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            citations: m.citations,
          })),
        );
      } catch (err) {
        toast.error(extractErrorMessage(err, "대화를 불러오지 못했습니다."));
        setMessages([]);
      } finally {
        setMessagesLoading(false);
      }
    },
    [toast],
  );

  const selectSession = (id: number) => {
    if (streaming || id === activeId) return;
    void openSession(id);
  };

  const startNewChat = () => {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    pendingRef.current = null;
  };

  // 메시지 추가/스트리밍 시 맨 아래로 스크롤.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  /** assistant 답변을 스트리밍한다(임시 말풍선 추가 → 토큰 누적 → done/error 로 확정). */
  const runStream = useCallback(
    async (sessionId: number, question: string) => {
      const tempId = nextTempId();
      pendingRef.current = { sessionId, question };
      setMessages((prev) => [
        ...prev,
        { id: tempId, role: "assistant", content: "", citations: null, streaming: true },
      ]);

      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);

      try {
        await streamMessage(
          sessionId,
          question,
          {
            onToken: (v) =>
              setMessages((prev) =>
                prev.map((m) => (m.id === tempId ? { ...m, content: m.content + v } : m)),
              ),
            onCitations: (c) =>
              setMessages((prev) =>
                prev.map((m) => (m.id === tempId ? { ...m, citations: c } : m)),
              ),
            onDone: (d) => {
              setMessages((prev) =>
                prev.map((m) => (m.id === tempId ? { ...m, id: d.message_id, streaming: false } : m)),
              );
              // 첫 질문이면 서버가 세션 제목을 설정한다 — 목록에 반영.
              setSessions((prev) =>
                prev.map((s) => (s.id === d.session_id ? { ...s, title: d.title } : s)),
              );
              pendingRef.current = null;
            },
            onError: (msg) => {
              setMessages((prev) =>
                prev.map((m) => (m.id === tempId ? { ...m, streaming: false, error: msg } : m)),
              );
              toast.error(msg);
            },
          },
          controller.signal,
        );
      } catch {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === tempId
              ? { ...m, streaming: false, error: "네트워크 오류로 답변을 받지 못했습니다." }
              : m,
          ),
        );
      } finally {
        setStreaming(false);
        abortRef.current = null;
        // 중단(abort)된 경우: 내용이 있으면 커서만 걷어내고, 아직 빈 말풍선이면 제거한다.
        setMessages((prev) =>
          prev.flatMap((m) => {
            if (m.id !== tempId || !m.streaming) return [m];
            return m.content ? [{ ...m, streaming: false }] : [];
          }),
        );
      }
    },
    [toast],
  );

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");

    let sessionId = activeId;
    if (sessionId == null) {
      try {
        const created = await createSession("", newWikiScope);
        sessionId = created.id;
        setActiveId(created.id);
        setSessions((prev) => [created, ...prev]);
      } catch (err) {
        toast.error(extractErrorMessage(err, "세션을 만들지 못했습니다."));
        setInput(text);
        return;
      }
    }

    setMessages((prev) => [
      ...prev,
      { id: nextTempId(), role: "user", content: text, citations: null },
    ]);
    await runStream(sessionId, text);
  };

  const handleRetry = () => {
    const pending = pendingRef.current;
    if (!pending || streaming) return;
    // 오류 말풍선을 걷어내고 같은 질문을 다시 스트리밍한다.
    setMessages((prev) => prev.filter((m) => !m.error));
    void runStream(pending.sessionId, pending.question);
  };

  const handleStop = () => {
    abortRef.current?.abort();
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteSession(deleteTarget.id);
      setSessions((prev) => prev.filter((s) => s.id !== deleteTarget.id));
      if (activeId === deleteTarget.id) {
        setActiveId(null);
        setMessages([]);
      }
      setDeleteTarget(null);
    } catch (err) {
      toast.error(extractErrorMessage(err, "세션을 삭제하지 못했습니다."));
    } finally {
      setDeleting(false);
    }
  };

  const openCitation = (c: ChatCitation) => {
    setPreview({ fileId: c.file_id, name: c.file_name || `파일 #${c.file_id}` });
  };

  // 답변 승격 — 서버 메시지(id:number)만 대상. 전사 위키 단일이라 대상 선택은 없다.
  const startPromote = (m: DisplayMessage) => {
    if (typeof m.id !== "number") return;
    setPromoteTitle("");
    setPromoteTarget(m);
  };

  const confirmPromote = async () => {
    if (!promoteTarget || typeof promoteTarget.id !== "number") return;
    const title = promoteTitle.trim();
    if (!title) return;
    setPromoting(true);
    try {
      const res = await promoteAnswer({ message_id: promoteTarget.id, title });
      toast.success(`"${res.name}" 페이지로 저장했습니다.`);
      setPromoteTarget(null);
    } catch (err) {
      toast.error(extractErrorMessage(err, "위키로 승격하지 못했습니다."));
    } finally {
      setPromoting(false);
    }
  };

  const activeSession = sessions.find((s) => s.id === activeId);

  return (
    <div className="flex h-screen">
      {/* 좌: 세션 목록 */}
      <aside className="flex w-64 shrink-0 flex-col border-r border-token bg-[color:var(--bg-secondary)]">
        <div className="flex items-center justify-between gap-2 px-4 py-4">
          <h1 className="text-base font-semibold">챗봇</h1>
          <button
            className="btn btn-primary px-2.5 py-1.5 text-xs"
            onClick={startNewChat}
            disabled={streaming}
          >
            <PlusIcon width={14} height={14} />
            새 대화
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
          {sessionsLoading ? (
            <LoadingState label="세션 불러오는 중..." />
          ) : sessionsError ? (
            <ErrorState message={sessionsError} onRetry={loadSessions} />
          ) : sessions.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted">
              아직 대화가 없습니다.
              <br />
              질문을 입력해 시작하세요.
            </p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {sessions.map((s) => (
                <li key={s.id}>
                  <div
                    className={`group flex items-center gap-1 rounded-lg px-2 ${
                      s.id === activeId
                        ? "bg-[color:var(--bg-muted)]"
                        : "hover:bg-[color:var(--bg-muted)]"
                    }`}
                  >
                    <button
                      className="min-w-0 flex-1 py-2 text-left"
                      onClick={() => selectSession(s.id)}
                      disabled={streaming && s.id !== activeId}
                    >
                      <p
                        className={`flex items-center gap-1 truncate text-sm ${
                          s.id === activeId
                            ? "font-medium text-[color:var(--accent)]"
                            : "text-[color:var(--text-secondary)]"
                        }`}
                        title={s.wiki_scope ? `${s.title || "제목 없음"} (위키 범위)` : s.title || "제목 없음"}
                      >
                        {s.wiki_scope && (
                          <BookIcon width={12} height={12} className="shrink-0 text-[color:var(--accent)]" />
                        )}
                        <span className="truncate">{s.title || "제목 없음"}</span>
                      </p>
                      <p className="truncate text-[0.7rem] text-muted">{formatDate(s.created_at)}</p>
                    </button>
                    <button
                      className="btn btn-ghost shrink-0 px-1.5 py-1 opacity-0 group-hover:opacity-100"
                      onClick={() => setDeleteTarget(s)}
                      disabled={streaming}
                      aria-label="세션 삭제"
                      title="세션 삭제"
                    >
                      <TrashIcon width={14} height={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* 우: 대화창 */}
      <section className="flex min-w-0 flex-1 flex-col">
        {/* 검색 범위: 새 대화면 위키 범위 토글, 진행 중 세션이면 확정된 범위 표시 */}
        <div className="flex items-center gap-2 border-b border-token px-4 py-2.5 text-sm">
          <span className="flex items-center gap-1.5 text-muted">
            <BookIcon width={15} height={15} />
            검색 범위
          </span>
          {activeId == null ? (
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={newWikiScope}
                onChange={(e) => setNewWikiScope(e.target.checked)}
              />
              <span>위키 범위로 한정</span>
              <span className="text-xs text-muted">
                {newWikiScope
                  ? "(공유 문서·위키 페이지만)"
                  : "(접근 가능한 모든 문서)"}
              </span>
            </label>
          ) : (
            <Badge tone={activeSession?.wiki_scope ? "accent" : "neutral"}>
              {activeSession?.wiki_scope ? "위키 범위" : "전체 범위"}
            </Badge>
          )}
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
          {messagesLoading ? (
            <LoadingState label="대화 불러오는 중..." />
          ) : messages.length === 0 ? (
            <div className="flex h-full items-center justify-center p-6">
              <EmptyState
                icon={<ChatIcon width={40} height={40} />}
                title="무엇이든 물어보세요"
                hint="접근 권한이 있는 문서를 근거로 답변하고 출처를 함께 보여줍니다."
              />
            </div>
          ) : (
            <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
              {messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  onCitation={openCitation}
                  onRetry={handleRetry}
                  onPromote={startPromote}
                />
              ))}
            </div>
          )}
        </div>

        {/* 입력 바 */}
        <div className="border-t border-token px-4 py-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              className="input max-h-40 min-h-[2.75rem] flex-1 resize-none py-2.5"
              placeholder="질문을 입력하세요… (Enter 전송, Shift+Enter 줄바꿈)"
              rows={1}
              value={input}
              disabled={streaming}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
            />
            {streaming ? (
              <button className="btn btn-secondary shrink-0" onClick={handleStop}>
                <StopIcon width={16} height={16} />
                중단
              </button>
            ) : (
              <button
                className="btn btn-primary shrink-0"
                onClick={() => void handleSend()}
                disabled={!input.trim()}
              >
                <SendIcon width={16} height={16} />
                전송
              </button>
            )}
          </div>
        </div>
      </section>

      {/* 삭제 확인 */}
      <Modal
        open={deleteTarget !== null}
        title="세션 삭제"
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
        <p className="text-sm text-muted">
          "{deleteTarget?.title || "제목 없음"}" 대화를 삭제할까요? 이 작업은 되돌릴 수 없습니다.
        </p>
      </Modal>

      {/* 출처 미리보기 — 권한 회수 파일(403/404)은 정중한 오류로 안내한다. */}
      <PreviewModal
        open={preview !== null}
        title={preview?.name ?? ""}
        onClose={() => setPreview(null)}
        load={() =>
          fetchFilePreview(preview!.fileId).catch((err) => {
            const s = errorStatus(err);
            if (s === 403 || s === 404) {
              const msg = "이 파일에 접근할 수 없습니다. 권한이 회수되었을 수 있습니다.";
              toast.error(msg);
              throw new Error(msg);
            }
            throw new Error(extractErrorMessage(err, "미리보기를 불러오지 못했습니다."));
          })
        }
        onDownload={preview ? () => void downloadFile(preview.fileId) : undefined}
      />

      {/* 답변을 위키 페이지로 승격 */}
      <Modal
        open={promoteTarget !== null}
        title="위키로 승격"
        onClose={() => setPromoteTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setPromoteTarget(null)}>
              취소
            </button>
            <button
              className="btn btn-primary"
              onClick={confirmPromote}
              disabled={promoting || !promoteTitle.trim()}
            >
              {promoting ? <Spinner className="h-4 w-4" /> : "승격"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">
            이 답변을 전사 위키 페이지로 저장합니다. 출처도 함께 기록됩니다. 승격하면 답변이{" "}
            <span className="font-medium text-[color:var(--warning)]">모든 사용자에게 공개</span>
            됩니다(인용 원본은 클릭 시 권한이 재검증됩니다).
          </p>
          <div>
            <label className="label" htmlFor="promoteTitle">
              페이지 제목
            </label>
            <input
              id="promoteTitle"
              className="input"
              placeholder="예: 배포 절차 요약"
              value={promoteTitle}
              onChange={(e) => setPromoteTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && confirmPromote()}
              autoFocus
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}

function MessageBubble({
  message,
  onCitation,
  onRetry,
  onPromote,
}: {
  message: DisplayMessage;
  onCitation: (c: ChatCitation) => void;
  onRetry: () => void;
  /** 답변 말풍선에 "위키로 승격" 버튼을 노출한다(전사 위키 단일이라 항상 가능). */
  onPromote?: (m: DisplayMessage) => void;
}) {
  const isUser = message.role === "user";
  // 서버 저장된 답변(id:number)만 승격 가능 — 스트리밍/오류 중 임시 말풍선은 제외.
  const canPromote =
    !isUser &&
    onPromote &&
    typeof message.id === "number" &&
    !message.streaming &&
    !message.error &&
    message.content.length > 0;
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`flex max-w-[85%] flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? "bg-[color:var(--accent)] text-[color:var(--accent-fg)]"
              : "card"
          }`}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
              {message.content}
            </p>
          ) : (
            <>
              {message.content ? (
                <Markdown text={message.content} />
              ) : message.streaming ? (
                <span className="flex items-center gap-2 text-sm text-muted">
                  <Spinner className="h-4 w-4 text-[color:var(--accent)]" />
                  답변을 준비하고 있어요…
                </span>
              ) : null}
              {message.streaming && message.content && (
                <span className="ml-0.5 inline-block animate-pulse font-medium">▍</span>
              )}
            </>
          )}
        </div>

        {/* 출처 칩 */}
        {!isUser && message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.citations.map((c, i) => (
              <button
                key={`${c.file_id}-${c.chunk_id}-${i}`}
                className="btn btn-secondary px-2 py-1 text-xs"
                onClick={() => onCitation(c)}
                title={c.snippet}
              >
                <FileIcon width={13} height={13} />
                <span className="max-w-[16rem] truncate">
                  {c.file_name || `파일 #${c.file_id}`}
                </span>
              </button>
            ))}
          </div>
        )}

        {/* 위키로 승격 */}
        {canPromote && (
          <button
            className="btn btn-ghost px-2 py-1 text-xs text-muted"
            onClick={() => onPromote?.(message)}
            title="이 답변을 위키 페이지로 저장합니다"
          >
            <BookIcon width={13} height={13} />
            위키로 승격
          </button>
        )}

        {/* 오류 + 재시도 */}
        {message.error && (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-danger">{message.error}</span>
            <button className="btn btn-secondary px-2 py-1 text-xs" onClick={onRetry}>
              다시 시도
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
