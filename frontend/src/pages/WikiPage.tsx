/**
 * 위키 질의 화면 (spec/wiki-index.md).
 *
 * 검색 대상은 **내가 접근할 수 있는 문서뿐**이다 — 백엔드가 대상 선정 단계에서 거르므로,
 * 권한 없는 문서의 본문은 모델 컨텍스트에 들어가지도 않는다. 그래서 이 화면은 "몇 건을
 * 뒤졌는지"를 함께 보여준다. 답이 없을 때 사용자가 "자료가 없는 것"과 "내 권한이 없는 것"을
 * 구분할 수 있어야 하기 때문이다.
 *
 * 답변에는 반드시 근거를 붙인다. 근거를 누르면 그 문서의 해당 위치를 미리보기로 연다 —
 * 앵커가 페이지가 아니라 **줄 번호**인 것은 md 트리의 좌표가 line_num 이기 때문이다.
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import type { WikiAnswer, WikiCitation, WikiDocumentItem } from "@/api/types";
import { askWiki, listWikiDocuments } from "@/api/wiki";
import { PageHeader } from "@/components/PageHeader";
import { PreviewModal } from "@/components/PreviewModal";
import { WikiStatusBadge } from "@/components/WikiStatusBadge";
import { EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { subscribeFileEvents } from "@/lib/fileEvents";
import { fetchFilePreview } from "@/lib/preview";

function CitationList({
  citations,
  onOpen,
}: {
  citations: WikiCitation[];
  onOpen: (c: WikiCitation) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-4">
      <h3 className="mb-2 text-xs font-semibold text-muted">근거</h3>
      <div className="flex flex-col gap-1.5">
        {citations.map((c) => (
          <button
            key={`${c.file_id}-${c.node_id}`}
            className="flex items-center justify-between gap-2 rounded-lg border border-token px-3 py-2 text-left text-sm hover:bg-muted-token"
            onClick={() => onOpen(c)}
          >
            <span className="min-w-0 truncate">
              <span className="font-medium">{c.file_name}</span>
              <span className="text-muted"> · {c.node_title}</span>
            </span>
            <span className="shrink-0 text-xs text-muted">{c.line_num}줄</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function WikiPage() {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<WikiAnswer | null>(null);
  const [askError, setAskError] = useState<string | null>(null);

  const [docs, setDocs] = useState<WikiDocumentItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [docsError, setDocsError] = useState<string | null>(null);

  const [preview, setPreview] = useState<WikiCitation | null>(null);

  const loadDocs = useCallback(async () => {
    setDocsError(null);
    try {
      const data = await listWikiDocuments(1, 100);
      setDocs(data.items);
    } catch (e) {
      setDocsError(extractErrorMessage(e));
    } finally {
      setDocsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  // 인덱싱이 끝나면 목록의 상태 배지가 따라 바뀌어야 한다. 워커가 발행하는 SSE 를 그대로 탄다.
  useEffect(() => {
    const stop = subscribeFileEvents({
      onEvent: (e) => {
        if (e.type === "wiki") void loadDocs();
      },
      onReconnect: () => void loadDocs(),
    });
    return stop;
  }, [loadDocs]);

  const onAsk = async () => {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setAskError(null);
    try {
      setAnswer(await askWiki(q));
    } catch (e) {
      setAskError(extractErrorMessage(e));
      setAnswer(null);
    } finally {
      setAsking(false);
    }
  };

  const readyCount = docs.filter((d) => d.status === "ready" || d.status === "stale").length;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader align="start">
        <h1 className="text-lg font-semibold">위키</h1>
        <p className="mt-0.5 text-sm text-muted">
          인덱싱된 문서에 물어보세요. 내가 열람할 수 있는 자료 안에서만 찾습니다.
        </p>
      </PageHeader>

      <div className="card p-4">
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="예: 배포 후 롤백은 어떤 기준으로 하나요?"
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
            {asking ? <Spinner className="h-4 w-4" /> : "질문"}
          </button>
        </div>
        {readyCount === 0 && !docsLoading && (
          <p className="mt-2 text-xs text-muted">
            아직 인덱싱된 문서가 없습니다. 드라이브에서 Markdown·HTML 파일의 &ldquo;위키
            설정&rdquo;을 켜면 여기에서 찾을 수 있게 됩니다.
          </p>
        )}
      </div>

      {askError && <ErrorState message={askError} onRetry={() => void onAsk()} />}

      {answer && !askError && (
        <div className="card p-4">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{answer.answer}</p>
          <CitationList citations={answer.citations} onOpen={setPreview} />
          <p className="mt-4 text-xs text-muted">
            내가 열람할 수 있는 문서 {answer.searched_documents}건에서 찾았습니다.
          </p>
        </div>
      )}

      <div className="card p-4">
        <h2 className="mb-3 text-sm font-semibold">인덱싱된 문서</h2>
        {docsLoading && <LoadingState />}
        {!docsLoading && docsError && (
          <ErrorState message={docsError} onRetry={() => void loadDocs()} />
        )}
        {!docsLoading && !docsError && docs.length === 0 && (
          <EmptyState
            title="아직 없습니다"
            hint="드라이브에서 파일의 위키 설정을 켜면 여기에 나타납니다."
          />
        )}
        {!docsLoading && !docsError && docs.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {docs.map((d) => (
              <div
                key={d.file_id}
                className="flex items-center justify-between gap-3 rounded-lg border border-token px-3 py-2 text-sm"
              >
                <span className="min-w-0 truncate">
                  <span className="font-medium">{d.name}</span>
                  <span className="text-muted"> · {d.owner_display_name}</span>
                </span>
                <span className="shrink-0">
                  <WikiStatusBadge status={d.status} nodeCount={d.node_count} />
                </span>
              </div>
            ))}
          </div>
        )}
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
