/**
 * 대화형 위키 질의 — 좌측 세션 목록 + 우측 대화.
 *
 * 단발 질의(`/wiki`, WikiPage)와 나란히 있다. 이쪽은 맥락이 이어져서 "그거 작년은?" 같은
 * 후속 질문이 통한다 — 서버가 최근 턴을 모델에 넘기고, 모델이 그것을 **독립형 검색 질의**로
 * 바꿔 검색한다(backend `services/chat/tools.py`).
 *
 * 답변의 **형태**는 서버가 정하지 않고 모델이 고른다. 마지막에 부른 렌더 툴의 인자가 그대로
 * `artifact` 로 내려오고, 이 화면은 `kind` 로 렌더러만 고른다(ArtifactView). 그래서 차트·
 * 리포트가 붙어도 이 파일은 거의 그대로다.
 *
 * 질문은 **낙관적으로** 먼저 그린다. Solar 는 추론 모델이라 답까지 수 초~수십 초가 걸리는데,
 * 그동안 자기가 뭘 물었는지 화면에 없으면 입력이 먹었는지조차 알 수 없다. 실패하면 서버가
 * 질문·답변을 한 트랜잭션으로 되돌리므로 화면에서도 함께 거둬들인다.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import {
  askChat,
  createChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions,
  renameChatSession,
} from "@/api/chat";
import type { ChatMessage, ChatSessionItem, WikiCitation } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { PreviewModal } from "@/components/PreviewModal";
import { ArtifactView } from "@/components/chat/ArtifactView";
import { CitationList } from "@/components/chat/CitationList";
import { ChatIcon, PlusIcon, RenameIcon, TrashIcon } from "@/components/icons";
import { EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { fetchFilePreview } from "@/lib/preview";

/** 낙관적으로 그린 질문의 임시 id. 서버 id 와 겹치지 않게 음수를 쓴다. */
const PENDING_ID = -1;

/** 제목 길이 상한. 서버(`chat_sessions.title`)와 같은 값이라야 잘리는 자리가 어긋나지 않는다. */
const TITLE_CHARS = 60;

/**
 * 첫 질문에서 제목을 만든다 — 서버 `services/chat/sessions.py` 의 `derive_title` 과 **같은 규칙**이다.
 * 규칙이 어긋나면 답이 도착하는 순간 목록의 글자가 바뀌어, 잘못 그렸다가 고친 것처럼 보인다.
 */
function deriveTitle(question: string): string {
  return question.split(/\s+/).filter(Boolean).join(" ").slice(0, TITLE_CHARS);
}

function SessionRow({
  session,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  session: ChatSessionItem;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== session.title) onRename(next);
  };

  if (editing) {
    return (
      <input
        className="input w-full text-sm"
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {
            setDraft(session.title);
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <div
      className={`group flex items-center gap-1 rounded-lg px-2 py-2 ${
        active ? "bg-muted-token" : "hover:bg-muted-token"
      }`}
    >
      <button className="min-w-0 flex-1 truncate text-left text-sm" onClick={onSelect}>
        {/*
          제목 없는 세션의 대체 문구를 "새 대화"로 두면 바로 위 생성 버튼과 글자가 겹쳐
          사이드바가 "새 대화새 대화"로 읽힌다 — 목록의 유일한 항목이 버튼의 일부처럼 보여
          히스토리가 없는 것처럼 느껴진다(실측). 행은 **상태**를, 버튼은 **동작**을 말해야 한다.
        */}
        {session.title || "제목 없음"}
      </button>
      {/* 액션은 hover·focus 에서만 드러낸다 — 목록이 아이콘으로 붐비면 제목이 안 읽힌다. */}
      <button
        className="btn btn-ghost shrink-0 p-1.5 opacity-0 group-hover:opacity-100 focus:opacity-100"
        title="제목 변경"
        onClick={() => {
          setDraft(session.title);
          setEditing(true);
        }}
      >
        <RenameIcon width={14} height={14} />
      </button>
      <button
        className="btn btn-ghost shrink-0 p-1.5 opacity-0 group-hover:opacity-100 focus:opacity-100"
        title="대화 삭제"
        onClick={onDelete}
      >
        <TrashIcon width={14} height={14} />
      </button>
    </div>
  );
}

function MessageBubble({
  message,
  onOpenCitation,
}: {
  message: ChatMessage;
  onOpenCitation: (c: WikiCitation) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        {/*
          질문은 채움색(--accent) 대신 muted 표면을 쓴다 — accent 는 버튼·프로그레스의 채움용
          이라 그 위의 글씨는 테마에 따라 대비가 모자란다(index.css 의 .text-accent 주석).
        */}
        <div className="max-w-[80%] rounded-2xl bg-muted-token px-4 py-2.5 text-sm whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="card p-4">
      <ArtifactView artifact={message.artifact} fallback={message.content} />
      <CitationList citations={message.citations} onOpen={onOpenCitation} />
      {/*
        어떤 질의로 검색했는지 드러낸다. 답이 엉뚱할 때 원인은 대개 "검색이 다른 걸
        가져왔다"이고, 그건 최종 답변만 봐서는 보이지 않는다.
      */}
      {message.tool_trace.length > 0 && (
        <p className="mt-3 text-xs text-muted">
          검색: {message.tool_trace.map((t) => `“${t.query}”`).join(", ")}
        </p>
      )}
    </div>
  );
}

export function ChatPage() {
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [scope, setScope] = useState<{ searched: number; examined: number } | null>(null);

  const [loadingList, setLoadingList] = useState(true);
  const [loadingThread, setLoadingThread] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<WikiCitation | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    const list = await listChatSessions();
    setSessions(list.items);
    return list.items;
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const items = await refreshSessions();
        if (items.length > 0) setActiveId(items[0].id);
      } catch (e) {
        setError(extractErrorMessage(e));
      } finally {
        setLoadingList(false);
      }
    })();
  }, [refreshSessions]);

  useEffect(() => {
    if (activeId === null) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoadingThread(true);
    void (async () => {
      try {
        const detail = await getChatSession(activeId);
        // 세션을 빠르게 갈아타면 늦게 온 응답이 새 대화를 덮어쓴다.
        if (!cancelled) {
          setMessages(detail.messages);
          setScope(null);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(extractErrorMessage(e));
      } finally {
        if (!cancelled) setLoadingThread(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, asking]);

  const onNewSession = async () => {
    try {
      const created = await createChatSession();
      setSessions((prev) => [created, ...prev]);
      setActiveId(created.id);
      setError(null);
    } catch (e) {
      setError(extractErrorMessage(e));
    }
  };

  const onRename = async (id: number, title: string) => {
    try {
      const updated = await renameChatSession(id, title);
      setSessions((prev) => prev.map((s) => (s.id === id ? updated : s)));
    } catch (e) {
      setError(extractErrorMessage(e));
    }
  };

  const onDelete = async (id: number) => {
    try {
      await deleteChatSession(id);
      const rest = sessions.filter((s) => s.id !== id);
      setSessions(rest);
      if (activeId === id) setActiveId(rest.length > 0 ? rest[0].id : null);
    } catch (e) {
      setError(extractErrorMessage(e));
    }
  };

  const onAsk = async () => {
    const q = question.trim();
    if (!q || asking) return;

    // 세션이 없으면 첫 질문이 세션을 만든다 — "새 대화"를 먼저 누르게 하지 않는다.
    let sessionId = activeId;
    // 낙관적으로 세운 제목을 되돌릴 대상. 실패하면 서버에도 제목이 남지 않으므로 함께 거둔다.
    let titledSessionId: number | null = null;
    setAsking(true);
    setError(null);
    try {
      let target = sessions.find((s) => s.id === sessionId);
      if (sessionId === null) {
        const created = await createChatSession();
        setSessions((prev) => [created, ...prev]);
        setActiveId(created.id);
        sessionId = created.id;
        target = created;
      }

      /*
        질문을 **목록에도 곧바로** 올린다. Solar 는 추론 모델이라 답까지 수십 초가 걸리는데,
        그동안 왼쪽이 "제목 없음"이면 목록에는 방금 무엇을 물었는지가 없다 — 화면에 도는 것은
        진행 표시뿐이라, 질문이 들어갔는지 목록만 보고는 알 수 없다(실측 지적).
        제목이 이미 있는 세션(이어 묻는 경우)은 건드리지 않는다 — 서버도 첫 질문에서만 짓는다.
      */
      if (target && !target.title) {
        const title = deriveTitle(q);
        titledSessionId = sessionId;
        setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, title } : s)));
      }

      setQuestion("");
      setMessages((prev) => [
        ...prev,
        {
          id: PENDING_ID,
          role: "user",
          content: q,
          artifact: null,
          citations: [],
          tool_trace: [],
          created_at: new Date().toISOString(),
        },
      ]);

      const result = await askChat(sessionId, q);
      // 임시 질문을 서버가 저장한 것으로 갈아 끼운다(id 가 생겨 key 가 안정된다).
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== PENDING_ID),
        result.question,
        result.answer,
      ]);
      setScope({
        searched: result.searched_documents,
        examined: result.examined_documents,
      });
      await refreshSessions();
    } catch (e) {
      // 서버가 질문·답변을 함께 되돌렸으므로 화면에서도 거둬들이고, 입력창에 되돌려 준다.
      setMessages((prev) => prev.filter((m) => m.id !== PENDING_ID));
      // 제목도 함께 거둔다 — 서버는 첫 질문과 한 트랜잭션으로 짓는다. 남겨 두면 새로고침에서
      // 사라져, 화면과 서버가 다른 말을 하는 구간이 생긴다.
      if (titledSessionId !== null) {
        setSessions((prev) =>
          prev.map((s) => (s.id === titledSessionId ? { ...s, title: "" } : s)),
        );
      }
      setQuestion(q);
      setError(extractErrorMessage(e));
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 pt-4 pb-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">채팅</h1>
          <p className="mt-0.5 text-sm text-muted">
            전사 위키 문서에 이어서 물어볼 수 있습니다. 답변에는 근거 문서와 위치가 붙습니다.
          </p>
        </PageHeader>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* 좌측 — 세션 목록 */}
        <aside className="flex w-64 shrink-0 flex-col border-r border-token">
          <div className="p-3">
            <button className="btn btn-primary w-full" onClick={() => void onNewSession()}>
              <PlusIcon width={16} height={16} />새 채팅
            </button>
          </div>
          {/*
            목록에 이름을 붙인다. 제목 없는 세션 하나만 있을 때 이 패널이 무엇인지 알 수
            없다는 지적이 있었다 — 비어 있을 때도 "여기가 지난 대화가 쌓이는 곳"이라는 것이
            보여야 한다.
          */}
          <h2 className="px-4 pb-1 text-xs font-semibold text-muted">지난 대화</h2>
          <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
            {loadingList ? (
              <LoadingState label="대화 목록" />
            ) : sessions.length === 0 ? (
              <p className="px-2 py-6 text-center text-xs text-muted">
                아직 대화가 없습니다.
              </p>
            ) : (
              <div className="flex flex-col gap-0.5">
                {sessions.map((s) => (
                  <SessionRow
                    key={s.id}
                    session={s}
                    active={s.id === activeId}
                    onSelect={() => setActiveId(s.id)}
                    onRename={(t) => void onRename(s.id, t)}
                    onDelete={() => void onDelete(s.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </aside>

        {/* 우측 — 대화 */}
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-auto p-6">
            {loadingThread ? (
              <LoadingState label="대화를 불러오는 중" />
            ) : messages.length === 0 && !asking ? (
              <EmptyState
                icon={<ChatIcon width={40} height={40} />}
                title="무엇이든 물어보세요"
                hint="이어서 물어보면 앞선 대화를 참고합니다. 비교를 물으면 표로 정리합니다."
              />
            ) : (
              <div className="mx-auto flex max-w-3xl flex-col gap-4">
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} onOpenCitation={setPreview} />
                ))}
                {asking && (
                  <div className="card flex items-center gap-2 p-4 text-sm text-muted">
                    <Spinner className="h-4 w-4" />
                    문서를 찾아 답을 만들고 있습니다…
                  </div>
                )}
                {/*
                  좁혀 본 경우를 숨기지 않는다. 문서가 많으면 질문과 관련된 절부터 예산만큼만
                  모델에 올리는데, 그것을 "전부 뒤졌다"로 읽으면 "없다"는 답을 과신한다.
                */}
                {scope && !asking && scope.searched > 0 && (
                  <p className="text-center text-xs text-muted">
                    {scope.examined > 0 && scope.examined < scope.searched
                      ? `위키 문서 ${scope.searched}건 중 질문과 관련된 ${scope.examined}건을 들여다봤습니다.`
                      : `위키 문서 ${scope.searched}건에서 찾았습니다.`}
                  </p>
                )}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="border-t border-token p-4">
            <div className="mx-auto max-w-3xl">
              {error && (
                <div className="mb-3">
                  <ErrorState message={error} />
                </div>
              )}
              <div className="flex gap-2">
                <input
                  className="input flex-1"
                  placeholder="예: 2026년 계획을 작년과 비교해 주세요"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onAsk();
                  }}
                  disabled={asking}
                />
                <button
                  className="btn btn-primary"
                  onClick={() => void onAsk()}
                  disabled={asking || question.trim().length === 0}
                >
                  {asking ? <Spinner className="h-4 w-4" /> : "보내기"}
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <PreviewModal
        open={preview !== null}
        title={preview ? `${preview.file_name} — ${preview.node_title}` : ""}
        onClose={() => setPreview(null)}
        load={() => fetchFilePreview(preview!.file_id)}
      />
    </div>
  );
}
