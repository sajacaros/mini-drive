/**
 * 문서용 마크다운 렌더러 — 미리보기 창에서 .md 파일을 서식대로 보여준다.
 *
 * 챗 답변용 `components/Markdown.tsx` 와는 쓰임이 다르다. 저쪽은 모델이 뱉는 몇 문단을 위한
 * 최소 렌더러(제목·리스트·굵게·인라인 코드)고, 이쪽은 사람이 쓴 **문서**를 상대한다 —
 * 표·코드 펜스·링크·인용·체크박스가 실제로 들어 있고, 그게 안 그려지면 원문 덩어리를 보는
 * 것과 다르지 않다. 그래서 GFM 을 지원하는 react-markdown 을 쓴다.
 *
 * 보안: `rehype-raw` 를 **넣지 않는다**. 그 상태의 react-markdown 은 원시 HTML 을 무시하고
 * (dangerouslySetInnerHTML 을 쓰지 않는다) `javascript:` 같은 URL 도 걸러낸다. 이 화면은
 * 무인증 공유 링크에서도 열리므로, 남이 올린 문서가 스크립트를 실을 구멍을 만들지 않는 게
 * 서식보다 우선이다.
 *
 * 색은 전부 테마 토큰을 쓴다 — 4테마(모던/게임보이 × 라이트/다크)에서 같이 살아야 한다.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownDoc({ text }: { text: string }) {
  return (
    <div className="markdown-doc text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 링크는 새 탭으로. rel 은 opener 탈취(무인증 공유에서 특히) 방지.
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // 표는 자기 상자 안에서 가로로 스크롤한다 — 넓은 표가 화면을 통째로 밀지 않게.
          table: ({ children, ...props }) => (
            <div className="my-3 overflow-x-auto">
              <table {...props}>{children}</table>
            </div>
          ),
          // 문서 안 이미지는 폭을 넘지 않게 가둔다(상대 경로는 깨질 수 있으므로 alt 를 남긴다).
          img: (props) => <img {...props} loading="lazy" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
