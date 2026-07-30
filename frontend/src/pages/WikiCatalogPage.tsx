/**
 * 문서 카탈로그 — 위키가 무엇을 알고 있는지 (spec/wiki-index.md 「프런트」).
 *
 * 질의 화면(WikiPage)에서 갈라져 나온 화면이다. 질문하는 사람은 이 목록이 필요 없지만, 문서를
 * 올리고 위키를 켜는 사람에게는 **답변의 재료를 확인할 곳**이 필요하다. 그 확인이 두 단계다:
 *   1) 이 화면 — 어떤 문서가 검색 대상인지, 색인이 끝났는지.
 *   2) 문서를 누르면 열리는 카탈로그(WikiCatalogDetailPage) — 그 문서가 어떤 절로 쪼개졌는지.
 *
 * 보이는 범위는 **전사 위키 전체**다 — 위키를 켜는 것이 곧 전사 공개이므로 사람마다 목록이
 * 다르지 않다. 그래서 백엔드도 문서별 권한 판정을 하지 않는다(spec 「왜 스위치가 하나인가」).
 *
 * 상태는 걸러내지 않는다. 대기·진행 중인 문서도(폴더를 켠 직후 하나씩 채워지는 것을 지켜볼 수
 * 있어야 한다), 꺼져서 질의 대상에서 빠진 문서도 **상태 그대로** 보인다 — 목록에서 사라지면
 * "왜 빠졌는지"를 알 수 없다. 그 상태 변화는 워커가 발행하는 SSE 로 배지에 따라붙는다.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import type { WikiDocumentItem } from "@/api/types";
import { listWikiDocuments } from "@/api/wiki";
import { PageHeader } from "@/components/PageHeader";
import { WikiStatusBadge, wikiStatusHint } from "@/components/WikiStatusBadge";
import { BookIcon, ChevronRightIcon } from "@/components/icons";
import { EmptyState, ErrorState, LoadingState, Pagination } from "@/components/ui";
import { subscribeFileEvents } from "@/lib/fileEvents";
import { formatDateTime } from "@/lib/format";

const PAGE_SIZE = 50;

export function WikiCatalogPage() {
  const navigate = useNavigate();

  const [page, setPage] = useState(1);
  const [docs, setDocs] = useState<WikiDocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /*
    검색어는 URL 에 둔다. 문서를 눌러 절 트리를 보고 돌아오는 것이 이 화면의 주 동선인데,
    지역 상태로 두면 돌아올 때 필터가 풀려 수백 건짜리 목록의 1페이지가 나온다 — 방금 보던
    문서가 사라진 것처럼 보인다. 겸사겸사 검색 결과 링크를 그대로 공유할 수 있다.
  */
  const [params, setParams] = useSearchParams();
  const query = params.get("q")?.trim() ?? "";
  // 입력값과 조회에 쓰는 값을 나눈다 — 한 글자마다 요청을 보내지 않기 위한 디바운스.
  const [search, setSearch] = useState(query);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await listWikiDocuments(page, PAGE_SIZE, query);
      setDocs(data.items);
      setTotal(data.total);
    } catch (e) {
      setError(extractErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [page, query]);

  // 이름 검색이 없으면 이 화면은 수백 건짜리 이름순 목록이고, 방금 켠 내 문서를 찾을 수 없다.
  useEffect(() => {
    const next = search.trim();
    if (next === query) return;
    const t = setTimeout(() => {
      // replace 로 넣는다 — 타이핑 한 번마다 히스토리가 쌓이면 뒤로 가기가 글자 지우기가 된다.
      setParams(next ? { q: next } : {}, { replace: true });
      setPage(1); // 필터가 바뀌면 3페이지에 머물러 빈 목록을 보는 일이 없어야 한다.
    }, 250);
    return () => clearTimeout(t);
  }, [search, query, setParams]);

  useEffect(() => {
    void load();
  }, [load]);

  // 인덱싱이 끝나면 상태 배지가 따라 바뀌어야 한다. 워커가 발행하는 SSE 를 그대로 탄다.
  useEffect(() => {
    const stop = subscribeFileEvents({
      onEvent: (e) => {
        if (e.type === "wiki") void load();
      },
      onReconnect: () => void load(),
    });
    return stop;
  }, [load]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  /*
    상세로 들어갈 때 검색어를 함께 넘긴다. 상세의 "문서 카탈로그" 브레드크럼이 이 값으로
    돌아와야 필터가 유지된다 — history 뒤로가기에 의존하면 링크로 직접 들어온 경우가 깨진다.
  */
  const openDoc = (fileId: number) =>
    navigate(`/wiki/catalog/${fileId}${query ? `?q=${encodeURIComponent(query)}` : ""}`);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader align="start">
          <h1 className="text-lg font-semibold">문서 카탈로그</h1>
          <p className="mt-0.5 text-sm text-muted">
            위키가 답할 때 뒤지는 문서입니다. 문서를 누르면 어떤 절로 쪼개져 있는지 볼 수 있습니다.
            {total > 0 && (query ? ` (검색 결과 ${total}건)` : ` (${total}건)`)}
          </p>
        </PageHeader>
        <input
          type="search"
          className="input mt-3 w-full max-w-sm"
          placeholder="문서명으로 찾기"
          aria-label="문서명으로 찾기"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : docs.length === 0 ? (
          /*
            "검색 결과 없음"과 "위키가 비었음"을 가른다. 검색 중에 "아직 없습니다"를 보여주면
            켜 둔 문서가 사라진 것으로 읽힌다 — 실제로는 이름이 안 맞은 것뿐이다.
          */
          query ? (
            <EmptyState
              icon={<BookIcon width={40} height={40} />}
              title="찾는 문서가 없습니다"
              hint={`"${query}" 와 이름이 맞는 문서가 없습니다. 검색어를 지우면 전체가 보입니다.`}
            />
          ) : (
            <EmptyState
              icon={<BookIcon width={40} height={40} />}
              title="아직 없습니다"
              hint="드라이브에서 Markdown·HTML 파일의 위키를 켜면 여기에 나타납니다."
            />
          )
        ) : (
          <>
            <div className="card overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-token text-left text-xs text-muted">
                    <th className="px-4 py-2.5 font-medium">문서</th>
                    <th className="w-36 px-4 py-2.5 font-medium">소유자</th>
                    <th className="w-16 px-4 py-2.5 font-medium">버전</th>
                    <th className="w-44 px-4 py-2.5 font-medium">색인 시각</th>
                    <th className="w-44 px-4 py-2.5 font-medium">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr
                      key={d.file_id}
                      className="cursor-pointer border-b border-token last:border-0 hover:bg-muted-token"
                      onClick={() => openDoc(d.file_id)}
                    >
                      <td className="px-4 py-2.5">
                        {/*
                          행 전체가 링크지만 키보드로도 들어갈 수 있어야 한다 — 이름 자체를
                          버튼으로 두고, 행 클릭은 그 버튼을 부르는 편의 경로로 남긴다.
                        */}
                        <span className="flex min-w-0 items-center gap-1.5">
                          <button
                            className="min-w-0 truncate text-left font-medium hover:text-accent"
                            title={d.name}
                            onClick={(e) => {
                              e.stopPropagation();
                              openDoc(d.file_id);
                            }}
                          >
                            {d.name}
                          </button>
                          <ChevronRightIcon width={14} height={14} className="shrink-0 text-muted" />
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-muted">{d.owner_display_name}</td>
                      <td className="px-4 py-2.5 tabular-nums text-muted">v{d.version}</td>
                      <td className="px-4 py-2.5 text-muted">
                        {d.indexed_at ? formatDateTime(d.indexed_at) : "—"}
                      </td>
                      <td className="px-4 py-2.5" title={wikiStatusHint(d.status)}>
                        <WikiStatusBadge status={d.status} nodeCount={d.node_count} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} totalPages={totalPages} onChange={setPage} />
          </>
        )}
      </div>
    </div>
  );
}
