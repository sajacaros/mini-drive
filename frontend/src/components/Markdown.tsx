/**
 * 아주 가벼운 마크다운 렌더러 (신규 의존성 없이 — PRD 7-2 지시).
 * 챗 답변 본문에 마크다운이 올 수 있어 제목(#)·리스트(-/1.)·문단·인라인(**굵게**·`코드`)만
 * 처리한다. React 엘리먼트로 직접 만들어 XSS 위험(dangerouslySetInnerHTML)을 피한다.
 */

import type { ReactNode } from "react";

/** 인라인 마크업(**굵게**, `코드`)을 React 노드로 토큰화한다. */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const regex = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<strong key={`${keyPrefix}-b${i}`}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      nodes.push(
        <code
          key={`${keyPrefix}-c${i}`}
          className="rounded bg-muted-token px-1 py-0.5 font-mono text-[0.85em]"
        >
          {m[2]}
        </code>,
      );
    }
    last = m.index + m[0].length;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const UL_RE = /^\s*[-*]\s+/;
const OL_RE = /^\s*\d+\.\s+/;
const H_RE = /^(#{1,3})\s+(.*)$/;

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i += 1;
      continue;
    }

    const heading = H_RE.exec(line);
    if (heading) {
      blocks.push(
        <p key={key++} className="text-[0.95rem] font-semibold">
          {renderInline(heading[2], `h${key}`)}
        </p>,
      );
      i += 1;
      continue;
    }

    if (UL_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && UL_RE.test(lines[i])) {
        items.push(lines[i].replace(UL_RE, ""));
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="list-disc space-y-0.5 pl-5">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `ul${key}-${j}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (OL_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && OL_RE.test(lines[i])) {
        items.push(lines[i].replace(OL_RE, ""));
        i += 1;
      }
      blocks.push(
        <ol key={key++} className="list-decimal space-y-0.5 pl-5">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `ol${key}-${j}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // 문단: 다음 빈 줄/리스트/제목 전까지 모아 개행을 보존해 렌더한다.
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !UL_RE.test(lines[i]) &&
      !OL_RE.test(lines[i]) &&
      !H_RE.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(
      <p key={key++} className="whitespace-pre-wrap break-words">
        {renderInline(para.join("\n"), `p${key}`)}
      </p>,
    );
  }

  return <div className="flex flex-col gap-2 text-sm leading-relaxed">{blocks}</div>;
}
