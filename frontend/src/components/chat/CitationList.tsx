/**
 * 답변 근거 목록. 위키 단발 질의(WikiPage)와 대화형 질의(ChatPage)가 공유한다.
 *
 * 누르면 그 문서의 해당 위치를 미리보기로 연다 — 앵커가 페이지가 아니라 **줄 번호**인 것은
 * md 트리의 좌표가 line_num 이기 때문이다.
 *
 * **접힌 채로 나온다.** 근거가 여러 건이면 답변보다 목록이 길어져, 대화를 이어 갈 때 앞선 답이
 * 근거 더미에 밀려 올라간다. 다만 **몇 건인지는 접힌 채로도 말한다** — 근거가 붙었다는 사실은
 * 답을 믿을지 정하는 데 필요하고, 그건 펴 보기 전에 알아야 한다. 접기 규약(버튼 + aria-expanded
 * + 쉐브론 회전)은 사이드바 섹션(Layout 의 NavSection)과 같게 둔다.
 */

import { useState } from "react";

import type { WikiCitation } from "@/api/types";
import { ChevronDownIcon } from "@/components/icons";

export function CitationList({
  citations,
  onOpen,
}: {
  citations: WikiCitation[];
  onOpen: (c: WikiCitation) => void;
}) {
  const [open, setOpen] = useState(false);

  if (citations.length === 0) return null;
  return (
    <div className="mt-4">
      {/* 제목 자체가 여닫이다 — 옆에 화살표를 따로 두면 누를 곳이 둘로 갈린다. */}
      <h3 className="text-xs font-semibold text-muted">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex items-center gap-1 rounded hover:text-[color:var(--text-primary)]"
        >
          근거 {citations.length}건
          <ChevronDownIcon
            width={12}
            height={12}
            className={`transition-transform ${open ? "" : "-rotate-90"}`}
          />
        </button>
      </h3>
      {open && (
        <div className="mt-2 flex flex-col gap-1.5">
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
      )}
    </div>
  );
}
