/**
 * 파일 미리보기 모달 (PRD 3.2). 이미지/PDF/텍스트를 인라인으로 보여주고, 미지원 형식(415)은
 * "미리보기 미지원 → 다운로드" 로 폴백한다. 인증 파일과 공개 공유 양쪽에서 재사용한다:
 * 실제 로딩은 `load` 콜백에 위임하고(경로별로 다름), 이 컴포넌트는 표시와 objectURL 수명만 맡는다.
 */

import { useEffect, useRef, useState } from "react";

import { type PreviewResult, releasePreview } from "@/lib/preview";
import { DownloadIcon, XIcon } from "./icons";
import { LoadingState } from "./ui";

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

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={onClose}
    >
      <div
        className="card flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden p-0"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between gap-3 border-b border-token px-5 py-3">
          <h2 className="min-w-0 truncate text-base font-semibold" title={title}>
            {title}
          </h2>
          <div className="flex shrink-0 items-center gap-2">
            {onDownload && (
              <button className="btn btn-secondary" onClick={onDownload}>
                <DownloadIcon width={16} height={16} />
                다운로드
              </button>
            )}
            <button className="btn btn-ghost px-2 py-1.5" onClick={onClose} aria-label="닫기">
              <XIcon width={18} height={18} />
            </button>
          </div>
        </div>

        {/* 본문 */}
        <div className="min-h-[240px] flex-1 overflow-auto bg-muted-token">
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
  if (result.kind === "image") {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <img src={result.url} alt="" className="max-h-[74vh] max-w-full object-contain" />
      </div>
    );
  }
  if (result.kind === "pdf") {
    return <iframe src={result.url} title="PDF 미리보기" className="h-[74vh] w-full" />;
  }
  if (result.kind === "text") {
    return (
      <div className="flex h-full flex-col">
        {result.truncated && (
          <p className="border-b border-token bg-[color:var(--bg-secondary)] px-4 py-2 text-xs text-muted">
            파일이 커서 앞부분만 표시됩니다. 전체 내용은 다운로드해 확인하세요.
          </p>
        )}
        <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed">
          {result.text}
        </pre>
      </div>
    );
  }
  // unsupported
  return (
    <PreviewMessage
      title="미리보기를 지원하지 않는 형식입니다"
      hint="이 파일 형식은 브라우저에서 미리 볼 수 없습니다. 다운로드해 확인하세요."
      onDownload={onDownload}
    />
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
    <div className="flex h-full flex-col items-center justify-center gap-3 p-10 text-center">
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
