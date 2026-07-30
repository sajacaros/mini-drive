/**
 * 문서 한 건의 카탈로그 — 위키가 이 문서를 어떻게 쪼개 알고 있는지 (spec/wiki-index.md).
 *
 * 여기 보이는 트리가 **검색이 보는 것 그대로**다. 질의는 절의 제목과 요약만 보고 답할 절을
 * 고르고(본문은 고른 뒤에 원문에서 잘라 온다), 그래서 답이 이상할 때 원문이 아니라 이 화면을
 * 봐야 원인이 보인다 — 절이 안 잡혔는지, 요약이 절을 잘못 대표하는지가 여기서 갈린다.
 *
 * 절을 누르면 원문 미리보기가 열린다. 앵커가 페이지가 아니라 **줄 번호**인 것은 md 트리의
 * 좌표가 line_num 이기 때문이다(질의 답변의 근거 클릭과 같은 경로).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import type { WikiCatalog, WikiCatalogNode } from "@/api/types";
import { getWikiCatalog } from "@/api/wiki";
import { PageHeader } from "@/components/PageHeader";
import { PreviewModal } from "@/components/PreviewModal";
import { WikiStatusBadge, wikiStatusHint } from "@/components/WikiStatusBadge";
import { BookIcon, ChevronRight } from "@/components/icons";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { fetchFilePreview } from "@/lib/preview";

/**
 * 화면에 쓸 요약. 짧은 절은 요약 대신 **본문이 그대로** 들어 있어(wiki_tree.SHORT_NODE_CHARS)
 * 첫 줄이 그 절의 헤더다 — 제목을 바로 위에 이미 보여줬으므로 걷어낸다. 저장된 트리는
 * 건드리지 않는다(검색은 헤더까지 포함된 그대로를 본다).
 */
function summaryText(summary: string | null): string | null {
  if (!summary) return null;
  const body = summary.replace(/^\s*#{1,6}\s+.*(\r?\n|$)/, "").trim();
  return body || null;
}

/** 절 하나 + 그 아래 하위 절. 깊이는 들여쓰기로만 표현한다(트리 선은 넣지 않는다). */
function CatalogNodeRow({
  node,
  depth,
  onOpen,
}: {
  node: WikiCatalogNode;
  depth: number;
  onOpen: (n: WikiCatalogNode) => void;
}) {
  const summary = summaryText(node.summary);
  return (
    <>
      <button
        className="flex w-full items-start gap-3 border-b border-token px-4 py-2.5 text-left last:border-0 hover:bg-muted-token"
        style={{ paddingLeft: `${16 + depth * 20}px` }}
        onClick={() => onOpen(node)}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{node.title}</span>
          {summary && (
            // 요약은 두 줄까지만 — 짧은 절은 요약 대신 본문이 그대로 들어 있어(wiki_tree)
            // 길이가 절마다 크게 다르다. 다 펼치면 트리의 모양이 안 보인다.
            <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-muted">
              {summary}
            </span>
          )}
        </span>
        <span className="shrink-0 pt-0.5 text-xs tabular-nums text-muted">{node.line_num}줄</span>
      </button>
      {node.nodes.map((child) => (
        <CatalogNodeRow key={child.node_id} node={child} depth={depth + 1} onOpen={onOpen} />
      ))}
    </>
  );
}

export function WikiCatalogDetailPage() {
  const { fileId } = useParams();
  const docId = Number(fileId);
  const navigate = useNavigate();
  /*
    목록에서 넘어올 때 검색어를 `?q=` 로 받는다. 브레드크럼이 그걸 그대로 되돌려줘야 필터가
    유지된다 — 목록은 수백 건이라 필터가 풀리면 방금 보던 문서가 사라진 것처럼 보인다.
  */
  const [params] = useSearchParams();
  const listQuery = params.get("q") ?? "";
  const backToList = `/wiki/catalog${listQuery ? `?q=${encodeURIComponent(listQuery)}` : ""}`;

  const [catalog, setCatalog] = useState<WikiCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<WikiCatalogNode | "document" | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCatalog(await getWikiCatalog(docId));
    } catch (e) {
      setError(extractErrorMessage(e, "문서를 찾을 수 없습니다."));
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    if (Number.isNaN(docId)) {
      setError("잘못된 문서입니다.");
      setLoading(false);
      return;
    }
    void load();
  }, [docId, load]);

  if (loading) {
    return (
      <div className="flex h-full flex-col p-6">
        <LoadingState />
      </div>
    );
  }

  if (error || !catalog) {
    return (
      <div className="flex h-full flex-col p-6">
        <ErrorState
          message={error ?? "문서를 찾을 수 없습니다."}
          onRetry={Number.isNaN(docId) ? undefined : () => void load()}
        />
      </div>
    );
  }

  const previewTitle =
    preview === "document" || preview === null
      ? catalog.name
      : `${catalog.name} — ${preview.title}`;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <nav className="mb-1 flex items-center gap-1 text-sm text-muted">
            <button
              className="hover:text-[color:var(--text-primary)]"
              onClick={() => navigate(backToList)}
            >
              문서 카탈로그
            </button>
            <ChevronRight width={14} height={14} />
            <span className="truncate font-medium text-[color:var(--text-primary)]">
              {catalog.name}
            </span>
          </nav>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-lg font-semibold">{catalog.name}</h1>
                <span title={wikiStatusHint(catalog.status)}>
                  <WikiStatusBadge status={catalog.status} nodeCount={catalog.node_count} />
                </span>
              </div>
              <p className="mt-0.5 text-sm text-muted">
                {catalog.owner_display_name} · v{catalog.version}
                {catalog.indexed_at && ` · ${formatDateTime(catalog.indexed_at)} 색인`}
              </p>
            </div>
            <button className="btn btn-secondary shrink-0" onClick={() => setPreview("document")}>
              원문 보기
            </button>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {catalog.nodes.length === 0 ? (
          <EmptyState
            icon={<BookIcon width={40} height={40} />}
            title="아직 절이 없습니다"
            hint={
              wikiStatusHint(catalog.status) ??
              "헤더가 없는 문서는 절로 쪼개지지 않습니다. 원문에 제목(#)을 넣으면 카탈로그가 생깁니다."
            }
          />
        ) : (
          <>
            <p className="mb-3 text-xs text-muted">
              질의는 이 절 제목과 요약만 보고 답할 곳을 고릅니다. 절을 누르면 원문을 볼 수 있습니다.
            </p>
            <div className="card overflow-hidden">
              {catalog.nodes.map((n) => (
                <CatalogNodeRow key={n.node_id} node={n} depth={0} onOpen={setPreview} />
              ))}
            </div>
          </>
        )}
      </div>

      <PreviewModal
        open={preview !== null}
        title={previewTitle}
        onClose={() => setPreview(null)}
        load={() => fetchFilePreview(catalog.file_id)}
      />
    </div>
  );
}
