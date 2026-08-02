/**
 * 답변 근거 목록. 위키 단발 질의(WikiPage)와 대화형 질의(ChatPage)가 공유한다.
 *
 * 누르면 그 문서의 해당 위치를 미리보기로 연다 — 앵커가 페이지가 아니라 **줄 번호**인 것은
 * md 트리의 좌표가 line_num 이기 때문이다.
 */

import type { WikiCitation } from "@/api/types";

export function CitationList({
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
