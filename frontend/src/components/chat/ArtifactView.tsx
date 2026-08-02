/**
 * 답변 아티팩트 렌더러 — `kind` 하나로 형태가 갈린다.
 *
 * 형태를 늘리는 일은 백엔드에 렌더 툴 하나(`services/chat/artifacts.py` 의 RENDERERS)와
 * 여기 분기 하나를 더하는 것이 전부다. 그래서 차트·리포트가 붙을 때 화면 전체를 다시
 * 짜지 않는다.
 *
 * **모르는 kind 를 만나면 깨지지 않고 안내로 떨어진다.** 백엔드가 먼저 배포되어 새 형태가
 * 내려오는 구간이 반드시 생기는데, 그때 흰 화면이 되면 안 된다.
 */

import type { ChatArtifact } from "@/api/types";
import { Markdown } from "@/components/Markdown";

function ComparisonTable({
  columns,
  rows,
  title,
  note,
}: Extract<ChatArtifact, { kind: "comparison" }>) {
  return (
    <div>
      {title && <h3 className="mb-2 text-sm font-semibold">{title}</h3>}
      {/*
        표는 자기 안에서만 가로로 스크롤한다 — 열이 많은 비교표가 페이지 전체를 가로로
        밀면 좌측 세션 목록까지 함께 밀린다.
      */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {columns.map((c, i) => (
                <th
                  key={i}
                  className="border-b border-token px-3 py-2 text-left font-semibold whitespace-nowrap"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="align-top">
                {row.map((cell, c) => (
                  <td
                    key={c}
                    className={`border-b border-token px-3 py-2 ${
                      c === 0 ? "font-medium" : "text-muted"
                    }`}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <p className="mt-2 text-xs text-muted">{note}</p>}
    </div>
  );
}

export function ArtifactView({
  artifact,
  fallback,
}: {
  artifact: ChatArtifact | null;
  /** 아티팩트가 없거나 모르는 형태일 때 보여줄 평문 — 서버가 항상 채워 보낸다. */
  fallback: string;
}) {
  if (artifact === null) return <Markdown text={fallback} />;

  switch (artifact.kind) {
    case "text":
      return <Markdown text={artifact.markdown} />;
    case "comparison":
      return <ComparisonTable {...artifact} />;
    default:
      // 이 화면보다 새로운 형태다. 평문 요약은 서버가 항상 채우므로 답 자체는 보인다.
      return (
        <div>
          <Markdown text={fallback} />
          <p className="mt-2 text-xs text-muted">
            이 답변은 새 형식으로 작성됐습니다. 화면을 새로고침하면 제대로 보일 수 있습니다.
          </p>
        </div>
      );
  }
}
