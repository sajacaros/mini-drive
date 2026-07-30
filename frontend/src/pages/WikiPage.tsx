/**
 * 위키 질의 화면 (spec/wiki-index.md).
 *
 * 이 화면은 **질문만** 한다. 무엇이 색인됐는지는 `/wiki/catalog`(WikiCatalogPage) 로 갈렸다 —
 * 질문하러 온 사람에게 인덱스 상태 목록은 답을 가리는 소음이고, 두 화면은 보는 빈도부터 다르다.
 *
 * 검색 대상은 **전사 위키 전체**다 — 위키를 켜는 것이 곧 전사 공개이므로 사람마다 다르지 않다.
 * 다만 문서가 많으면 질문과 관련된 절부터 예산만큼만 모델에 올린다(spec 「후보 선별」). 그래서
 * 이 화면은 대상 건수와 **실제로 들여다본 건수**를 함께 보여준다. 좁혀 본 것을 "전부 뒤졌다"로
 * 읽으면 "없다"는 답을 과신하게 된다.
 *
 * 답변에는 반드시 근거를 붙인다. 근거를 누르면 그 문서의 해당 위치를 미리보기로 연다 —
 * 앵커가 페이지가 아니라 **줄 번호**인 것은 md 트리의 좌표가 line_num 이기 때문이다.
 *
 * 대화 히스토리·세션은 아직 없다(백엔드도 단발 `POST /wiki/ask` 뿐이다). 붙일 때 이 화면이
 * 좌측 세션 목록 + 우측 대화로 갈라지도록, 질문 입력과 답변 표시를 한 덩어리로 묶어 둔다.
 */

import { useState } from "react";

import { extractErrorMessage } from "@/api/client";
import type { WikiAnswer, WikiCitation } from "@/api/types";
import { askWiki } from "@/api/wiki";
import { PageHeader } from "@/components/PageHeader";
import { PreviewModal } from "@/components/PreviewModal";
import { ChatIcon } from "@/components/icons";
import { EmptyState, ErrorState, Spinner } from "@/components/ui";
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

  const [preview, setPreview] = useState<WikiCitation | null>(null);

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

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 pt-4 pb-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">위키</h1>
          <p className="mt-0.5 text-sm text-muted">
            전사 위키에 올라온 문서에 물어보세요. 답변에는 근거 문서와 위치가 붙습니다.
          </p>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="flex flex-col gap-5">
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
          </div>

          {askError && <ErrorState message={askError} onRetry={() => void onAsk()} />}

          {answer && !askError && (
            <div className="card p-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{answer.answer}</p>
              <CitationList citations={answer.citations} onOpen={setPreview} />
              <p className="mt-4 text-xs text-muted">
                {/*
                  좁혀 본 경우를 숨기지 않는다. 문서가 많으면 질문과 관련된 절부터 예산만큼만
                  모델에 올리는데(spec 「후보 선별」), 그것을 "전부 뒤졌다"로 읽으면 "없다"는
                  답을 과신한다. 두 숫자가 같을 때만 한 문장으로 접는다.
                */}
                {answer.examined_documents > 0 &&
                answer.examined_documents < answer.searched_documents
                  ? `위키 문서 ${answer.searched_documents}건 중 질문과 관련된 ${answer.examined_documents}건을 들여다봤습니다.`
                  : `위키 문서 ${answer.searched_documents}건에서 찾았습니다.`}
                {/*
                  뒤질 문서가 0 건이면 "답이 없다"가 아니라 "찾을 곳이 없다"이다. 인덱싱 문서
                  목록을 이 화면에서 걷어낸 대신, 이 경우에만 그쪽으로 길을 낸다.
                */}
                {answer.searched_documents === 0 &&
                  " 드라이브에서 Markdown·HTML 파일의 “위키 설정”을 켜면 여기에서 찾을 수 있게 됩니다."}
              </p>
            </div>
          )}

          {!answer && !askError && !asking && (
            <EmptyState
              icon={<ChatIcon width={40} height={40} />}
              title="무엇이든 물어보세요"
              hint="답변에는 근거 문서와 위치가 함께 붙습니다."
            />
          )}
        </div>
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
