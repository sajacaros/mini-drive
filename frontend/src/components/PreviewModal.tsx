/**
 * 파일 미리보기 모달 (PRD 3.2). 이미지/PDF/텍스트/마크다운/영상(mp4)을 인라인으로 보여주고,
 * 미지원 형식(415)은 "미리보기 미지원 → 다운로드" 로 폴백한다. 인증 파일과 공개 공유 양쪽에서
 * 재사용한다: 실제 로딩은 `load` 콜백에 위임하고(경로별로 다름), 이 컴포넌트는 표시와
 * objectURL 수명만 맡는다.
 *
 * 좁은 화면에서는 창이 아니라 **전체 화면**으로 연다. 390px 짜리 화면에서 바깥 여백과
 * 카드 테두리까지 물리면 정작 문서에 남는 폭이 300px 밑으로 떨어진다 — 읽으라고 여는
 * 화면에서 읽을 자리를 여백에 내주는 셈이었다.
 *
 * 넓은 화면에서 크기는 **내용 종류가 정한다**. 텍스트나 안내 메시지는 지금처럼 내용만큼만
 * 차지하면 되지만, PDF·영상 뷰어는 자기 높이를 스스로 못 정한다(특히 canvas·iframe 은 부모가
 * 높이를 안 주면 브라우저 기본값으로 쪼그라든다). 그래서 그 둘일 때만 카드에 실제 높이를
 * 주고 폭도 넓혀, 뷰어가 화면을 채우게 한다.
 */

import { Suspense, lazy, useEffect, useRef, useState } from "react";

import { MarkdownDoc } from "@/components/MarkdownDoc";
import { useIsNarrow } from "@/lib/media";
import { type PreviewResult, releasePreview } from "@/lib/preview";
import { DownloadIcon, XIcon } from "./icons";
import { LoadingState } from "./ui";

// pdf.js 는 무겁다(워커까지 합쳐 수백 KB). PDF 를 실제로 열 때만 받아 오도록 갈라 둔다 —
// 파일 목록만 훑는 사람이 PDF 엔진을 내려받을 이유는 없다.
const PdfViewer = lazy(() => import("./PdfViewer"));

interface PreviewModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  /** 미리보기 데이터를 불러오는 콜백(경로별 구현). 오류는 throw 하면 화면에 안내된다. */
  load: () => Promise<PreviewResult>;
  /** 있으면 다운로드 버튼/폴백을 노출한다. 읽기 전용 등으로 다운로드 불가면 생략. */
  onDownload?: () => void;
}

export function PreviewModal({ open, title, onClose, load, onDownload }: PreviewModalProps) {
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const narrow = useIsNarrow();

  // load 콜백은 부모 렌더마다 새 참조가 될 수 있으므로 ref 로 잡아 effect 재실행을 막는다.
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let loaded: PreviewResult | null = null;
    setLoading(true);
    setError(null);
    setResult(null);
    void (async () => {
      try {
        const res = await loadRef.current();
        if (cancelled) {
          releasePreview(res);
          return;
        }
        loaded = res;
        setResult(res);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "미리보기를 불러오지 못했습니다.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      releasePreview(loaded);
    };
    // title 이 바뀌면(대상 파일 변경) 다시 로드한다.
  }, [open, title]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  // 뷰어형 내용(PDF/영상)은 카드에 실제 높이를 받아야 제 크기로 펼쳐진다.
  const fill = result?.kind === "pdf" || result?.kind === "video";

  return (
    <div
      className={`fixed inset-0 z-40 flex items-center justify-center bg-black/50 ${
        narrow ? "" : "p-4"
      }`}
      onMouseDown={onClose}
    >
      <div
        className={`card flex w-full flex-col overflow-hidden p-0 ${
          narrow
            ? "h-[100dvh] max-w-none rounded-none border-0"
            : fill
              ? "h-[92vh] max-w-6xl"
              : "max-h-[88vh] max-w-4xl"
        }`}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {/* 헤더 */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-token px-4 py-2.5 md:px-5 md:py-3">
          <h2 className="min-w-0 truncate text-base font-semibold" title={title}>
            {title}
          </h2>
          <div className="flex shrink-0 items-center gap-1 md:gap-2">
            {onDownload && (
              <button className="btn btn-secondary px-2.5 md:px-3.5" onClick={onDownload}>
                <DownloadIcon width={16} height={16} />
                {/* 좁은 화면에서는 라벨을 접는다 — 제목이 쓸 폭을 버튼이 가져가지 않게. */}
                <span className="hidden sm:inline">다운로드</span>
              </button>
            )}
            <button className="icon-btn btn-ghost p-2" onClick={onClose} aria-label="닫기">
              <XIcon width={18} height={18} />
            </button>
          </div>
        </div>

        {/* 본문 */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-muted-token">
          {loading ? (
            <LoadingState label="미리보기 불러오는 중..." />
          ) : error ? (
            <PreviewMessage
              title="미리보기를 불러오지 못했습니다"
              hint={error}
              onDownload={onDownload}
            />
          ) : result ? (
            <PreviewBody result={result} onDownload={onDownload} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function PreviewBody({
  result,
  onDownload,
}: {
  result: PreviewResult;
  onDownload?: () => void;
}) {
  if (result.kind === "image") return <ImagePreview url={result.url} />;
  if (result.kind === "pdf") {
    return (
      <Suspense fallback={<LoadingState label="PDF 뷰어 준비 중..." />}>
        {/* key: 다른 파일을 열면 뷰어를 새로 마운트한다(페이지 위치·배율·문서 상태를 함께 비운다). */}
        <PdfViewer
          key={result.url}
          blob={result.blob}
          url={result.url}
          onDownload={onDownload}
        />
      </Suspense>
    );
  }
  if (result.kind === "video") return <VideoPreview result={result} onDownload={onDownload} />;
  if (result.kind === "text") {
    return (
      <TextPreview
        text={result.text}
        truncated={result.truncated}
        markdown={result.textKind === "markdown"}
        onDownload={onDownload}
      />
    );
  }
  // unsupported — 다운로드가 없는 경로(읽기 전용 공유)에서는 다운로드를 권하지 않는다.
  return (
    <PreviewMessage
      title="미리보기를 지원하지 않는 형식입니다"
      hint={
        onDownload
          ? "이 파일 형식은 브라우저에서 미리 볼 수 없습니다. 다운로드해 확인하세요."
          : "이 파일 형식은 브라우저에서 미리 볼 수 없습니다."
      }
      onDownload={onDownload}
    />
  );
}

/**
 * 텍스트/마크다운. 마크다운은 기본이 **서식**이고, 원문 토글로 되돌릴 수 있다 —
 * 문서를 읽으러 온 사람에게는 서식이 맞지만, 표가 왜 안 그려지는지 보려면 원문이 필요하다.
 */
function TextPreview({
  text,
  truncated,
  markdown,
  onDownload,
}: {
  text: string;
  truncated: boolean;
  markdown: boolean;
  onDownload?: () => void;
}) {
  const [raw, setRaw] = useState(false);
  const rendered = markdown && !raw;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {(truncated || markdown) && (
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-token bg-[color:var(--bg-secondary)] px-4 py-2">
          <p className="text-xs text-muted">
            {truncated
              ? `파일이 커서 앞부분만 표시됩니다.${
                  onDownload ? " 전체 내용은 다운로드해 확인하세요." : ""
                }`
              : "마크다운 문서"}
          </p>
          {markdown && (
            <button
              className="btn btn-secondary px-2.5 py-1 text-xs"
              onClick={() => setRaw((v) => !v)}
              aria-pressed={raw}
            >
              {raw ? "서식 보기" : "원문 보기"}
            </button>
          )}
        </div>
      )}
      {rendered ? (
        <div className="min-h-0 flex-1 overflow-auto bg-[color:var(--bg-secondary)] px-4 py-4 md:px-6">
          <MarkdownDoc text={text} />
        </div>
      ) : (
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-[13px] leading-relaxed">
          {text}
        </pre>
      )}
    </div>
  );
}

/**
 * 이미지. 기본은 화면에 맞춤이고, 누르면 원본 크기로 펼쳐 상자 안에서 스크롤한다 —
 * 스크린샷·도면처럼 글씨가 들어간 이미지는 맞춤 상태에서 읽을 수 없다.
 */
function ImagePreview({ url }: { url: string }) {
  const [actual, setActual] = useState(false);
  return (
    <div className={`min-h-0 flex-1 ${actual ? "overflow-auto p-4" : "flex items-center justify-center overflow-hidden p-4"}`}>
      <button
        type="button"
        onClick={() => setActual((v) => !v)}
        title={actual ? "화면에 맞추기" : "원본 크기로 보기"}
        aria-label={actual ? "화면에 맞추기" : "원본 크기로 보기"}
        className={actual ? "cursor-zoom-out" : "cursor-zoom-in"}
      >
        <img
          src={url}
          alt=""
          className={actual ? "max-w-none" : "max-h-full max-w-full object-contain"}
        />
      </button>
    </div>
  );
}

/**
 * 영상 재생. src 는 objectURL 이 아니라 백엔드 스트림 주소라, 브라우저가 Range 로 필요한 구간만
 * 받아 온다(전체를 받고 나서 시작하지 않는다). 자동재생은 하지 않는다 — 공유 링크를 연 사람이
 * 소리부터 나는 걸 원하진 않는다.
 *
 * 재생 도중 티켓이 만료되거나 공유가 비활성화되면 구간 이동이 실패하는데, 그때 화면이 먹통이
 * 되지 않도록 오류를 잡아 다운로드 안내로 떨어뜨린다.
 */
function VideoPreview({
  result,
  onDownload,
}: {
  result: Extract<PreviewResult, { kind: "video" }>;
  onDownload?: () => void;
}) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <PreviewMessage
        title="영상을 재생하지 못했습니다"
        hint={
          onDownload
            ? "재생 시간이 만료됐거나 링크가 더 이상 유효하지 않을 수 있습니다. 새로고침하거나 다운로드해 확인하세요."
            : "재생 시간이 만료됐거나 링크가 더 이상 유효하지 않을 수 있습니다. 새로고침해 보세요."
        }
        onDownload={onDownload}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center bg-black p-2">
      <video
        src={result.url}
        controls
        // 읽기 전용 공유에서 브라우저 기본 플레이어의 다운로드 버튼이 서버 차단을 우회하지
        // 못하게 한다. 스트림 주소를 직접 긁으면 그만이라 유출 방지는 아니고, 다운로드
        // 동선을 지우지 않는 UI 를 남겨 두지 않기 위한 것이다.
        controlsList="nodownload"
        preload="metadata"
        playsInline
        className="max-h-full w-full"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function PreviewMessage({
  title,
  hint,
  onDownload,
}: {
  title: string;
  hint: string;
  onDownload?: () => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-10 text-center">
      <span className="text-4xl opacity-40">📄</span>
      <p className="font-medium">{title}</p>
      <p className="max-w-sm text-sm text-muted">{hint}</p>
      {onDownload && (
        <button className="btn btn-primary mt-1" onClick={onDownload}>
          <DownloadIcon width={16} height={16} />
          다운로드
        </button>
      )}
    </div>
  );
}
