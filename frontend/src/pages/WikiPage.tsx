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
 * 맥락이 이어지는 대화는 **별도 화면**(ChatPage, `/chat`)으로 갈렸다. 이 화면은 세션을 만들지
 * 않고 한 번 묻고 마는 경로로 남는다 — 답 하나만 보면 되는 사람에게 세션 목록은 소음이다.
 * 근거 목록은 두 화면이 `components/chat/CitationList` 를 공유한다.
 */

import { useState } from "react";

import { extractErrorMessage } from "@/api/client";
import type { WikiAnswer, WikiCitation } from "@/api/types";
import { askWiki } from "@/api/wiki";
import { PageHeader } from "@/components/PageHeader";
import { PreviewModal } from "@/components/PreviewModal";
import { CitationList } from "@/components/chat/CitationList";
import { ChatIcon } from "@/components/icons";
import { EmptyState, ErrorState, Spinner } from "@/components/ui";
import { fetchFilePreview } from "@/lib/preview";

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
