/**
 * PDF 뷰어 — pdf.js 로 페이지를 canvas 에 직접 그린다.
 *
 * 원래는 `<iframe src={blobURL}>` 로 브라우저 내장 뷰어에 맡겼는데, 그게 두 군데서 깨졌다:
 *   - 모바일: Android Chrome 은 iframe 안의 PDF 를 인라인으로 렌더하지 않고, iOS Safari 는
 *     blob 을 물린 iframe 을 자주 빈 화면으로 그린다. 즉 휴대폰에서는 늘 백지였다.
 *   - PC: iframe 은 이미지와 달리 고유 높이가 없어서, 높이를 지정받지 못하면 브라우저 기본값
 *     150px 로 주저앉는다. 모달이 내용 높이로 줄던 탓에 문서가 우표만 하게 나왔다.
 * pdf.js 에 blob 의 바이트를 그대로 넘기면 blob URL 을 거치지 않으므로 두 제약을 다 비켜간다.
 *
 * 화면 정책:
 *   - 기본 배율은 **폭 맞춤** — 문서를 읽으러 연 화면이니 가로가 꽉 차야 한다. 컨테이너 폭을
 *     ResizeObserver 로 재서 회전·창 크기 변경에도 다시 맞춘다.
 *   - 페이지는 보이는 것 주변만 그리고, 멀어지면 놓아 준다(IntersectionObserver). 열자마자
 *     전부 그리면 긴 문서에서 휴대폰이 멈추고, 그린 걸 계속 들고 있으면 canvas 메모리가
 *     수백 MB 로 쌓인다.
 *   - canvas 는 devicePixelRatio 를 곱해 그린다(2배까지). 안 그러면 고해상도 화면에서 글자가
 *     뭉갠 것처럼 보인다.
 *
 * canvas 렌더링이라 텍스트 선택·브라우저 내 검색은 되지 않는다. 그게 필요한 사람을 위해
 * "새 탭에서 열기"(브라우저 내장 뷰어)를 툴바에 상시 남겨 둔다.
 */

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { withBase } from "@/lib/basePath";
import { DownloadIcon, ExternalLinkIcon, ZoomInIcon, ZoomOutIcon } from "./icons";
import { LoadingState } from "./ui";

// 워커는 Vite 가 에셋으로 뽑아 준 주소를 쓴다(서브패스 배포에서도 base 가 붙는다).
GlobalWorkerOptions.workerSrc = workerUrl;

/** 폭 맞춤(=1)을 기준으로 한 배율 단계. */
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

/** 페이지 사이/가장자리 여백(px). 폭 맞춤 계산에서 빼야 가로 스크롤이 생기지 않는다. */
const GUTTER = 24;

interface PdfViewerProps {
  /** 미리보기로 받아 온 PDF 바이트. pdf.js 에 직접 넘긴다. */
  blob: Blob;
  /** 같은 파일의 objectURL — "새 탭에서 열기"(브라우저 내장 뷰어)용. */
  url: string;
  /** 있으면 오류 폴백에서 다운로드를 권한다. */
  onDownload?: () => void;
}

export default function PdfViewer({ blob, url, onDownload }: PdfViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [aspect, setAspect] = useState(297 / 210); // 첫 페이지를 재기 전 자리표시자(A4 세로)
  const [error, setError] = useState<string | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [zoomIndex, setZoomIndex] = useState(ZOOM_STEPS.indexOf(1));
  const [current, setCurrent] = useState(1);

  // 문서 로드. blob → ArrayBuffer 는 pdf.js 가 버퍼를 가져가므로(detach) 로드마다 새로 만든다.
  // 다른 파일을 열면 부모가 key 로 이 컴포넌트를 새로 마운트하므로, 여기서 상태를 되돌릴 일은 없다.
  useEffect(() => {
    let cancelled = false;
    // 문서가 아니라 **로딩 작업**을 잡아 둔다 — 워커까지 정리하는 destroy() 는 이쪽에 있다.
    let loading: PDFDocumentLoadingTask | null = null;

    void (async () => {
      try {
        const bytes = new Uint8Array(await blob.arrayBuffer());
        loading = getDocument({
          data: bytes,
          // 폰트를 임베드하지 않은 PDF 대비(한글 CID 폰트 포함). vite.config.ts 가 public/ 로 복사한다.
          cMapUrl: withBase("/pdfjs/cmaps/"),
          cMapPacked: true,
          standardFontDataUrl: withBase("/pdfjs/standard_fonts/"),
        });
        const opened = await loading.promise;
        if (cancelled) return;
        const first = await opened.getPage(1);
        const view = first.getViewport({ scale: 1 });
        if (cancelled) return;
        // 페이지 크기는 첫 장으로 어림잡는다. 전 페이지를 미리 재면 긴 문서에서 열리기까지
        // 눈에 띄게 느려지고, 실제 크기는 각 페이지가 그려질 때 canvas 가 바로잡는다.
        setAspect(view.height / view.width);
        setDoc(opened);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "PDF 를 여는 데 실패했습니다.");
        }
      }
    })();

    return () => {
      cancelled = true;
      void loading?.destroy();
    };
  }, [blob]);

  // 컨테이너 폭 추적 — 폭 맞춤의 기준. 창 크기 변경/기기 회전/스크롤바 등장에 모두 반응한다.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setViewportWidth(el.clientWidth));
    observer.observe(el);
    setViewportWidth(el.clientWidth);
    return () => observer.disconnect();
  }, [doc]);

  const zoom = ZOOM_STEPS[zoomIndex];
  const pageWidth = Math.max(120, Math.round((viewportWidth - GUTTER) * zoom));

  const onEnterView = useCallback((pageNumber: number) => setCurrent(pageNumber), []);

  if (error) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-10 text-center">
        <span className="text-4xl opacity-40">📄</span>
        <p className="font-medium">PDF 를 열지 못했습니다</p>
        <p className="max-w-sm text-sm text-muted">
          파일이 손상됐거나 암호가 걸려 있을 수 있습니다. 새 탭에서 열거나 다운로드해 확인하세요.
        </p>
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
          <button className="btn btn-secondary" onClick={() => openInNewTab(url)}>
            <ExternalLinkIcon width={16} height={16} />새 탭에서 열기
          </button>
          {onDownload && (
            <button className="btn btn-primary" onClick={onDownload}>
              <DownloadIcon width={16} height={16} />
              다운로드
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 툴바 — 페이지 위치, 배율, 내장 뷰어로 탈출. */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-token bg-[color:var(--bg-secondary)] px-2 py-1.5 md:px-4">
        <p className="shrink-0 text-xs text-muted tabular-nums">
          {doc ? `${current} / ${doc.numPages}` : " "}
        </p>
        <div className="flex items-center gap-1">
          <button
            className="icon-btn btn-ghost p-1.5"
            onClick={() => setZoomIndex((i) => Math.max(0, i - 1))}
            disabled={zoomIndex === 0}
            aria-label="축소"
            title="축소"
          >
            <ZoomOutIcon width={16} height={16} />
          </button>
          {/* 배율 표시를 겸한 "폭 맞춤" 되돌리기 버튼. */}
          <button
            className="btn btn-ghost min-w-14 px-1 py-1 text-xs tabular-nums"
            onClick={() => setZoomIndex(ZOOM_STEPS.indexOf(1))}
            title="폭 맞춤"
          >
            {zoom === 1 ? "맞춤" : `${Math.round(zoom * 100)}%`}
          </button>
          <button
            className="icon-btn btn-ghost p-1.5"
            onClick={() => setZoomIndex((i) => Math.min(ZOOM_STEPS.length - 1, i + 1))}
            disabled={zoomIndex === ZOOM_STEPS.length - 1}
            aria-label="확대"
            title="확대"
          >
            <ZoomInIcon width={16} height={16} />
          </button>
          <button
            className="btn btn-secondary ml-1 px-2 py-1 text-xs"
            onClick={() => openInNewTab(url)}
            title="브라우저 내장 뷰어로 엽니다 (텍스트 검색·선택·인쇄)"
          >
            <ExternalLinkIcon width={14} height={14} />
            <span className="hidden sm:inline">새 탭에서 열기</span>
          </button>
        </div>
      </div>

      {/* 페이지 스크롤 영역 */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto bg-muted-token">
        {doc ? (
          <div className="mx-auto flex w-fit flex-col items-center gap-3 p-3">
            {Array.from({ length: doc.numPages }, (_, i) => (
              <PdfPage
                key={i + 1}
                doc={doc}
                pageNumber={i + 1}
                width={pageWidth}
                aspect={aspect}
                rootRef={scrollRef}
                onEnterView={onEnterView}
              />
            ))}
          </div>
        ) : (
          <LoadingState label="PDF 여는 중..." />
        )}
      </div>
    </div>
  );
}

/**
 * 한 페이지. 뷰포트 근처(±500px)에 있는 동안만 canvas 를 둔다.
 *
 * 들어올 때 그리는 이유는 분명하다 — 열자마자 전 페이지를 그리면 긴 문서에서 휴대폰이 멈춘다.
 * **나갈 때 놓아 주는** 이유는 덜 분명한데 이쪽이 더 물린다: canvas 는 폭×높이×DPR²×4바이트를
 * 실제로 붙들고 있어서, 데스크톱 폭(1152px)·DPR 2 의 A4 한 장이 30MB 다. 40 장짜리 덱을
 * 끝까지 넘기면 1GB 가 쌓인다. 되돌아갈 때 한 장 다시 그리는 비용(수십 ms)이 그걸 이고 있는
 * 것보다 훨씬 싸다.
 *
 * 자리표시자 높이는 **자기 페이지 비율**로 잡는다(재기 전에는 첫 장 비율). 그래야 canvas 가
 * 붙었다 떨어져도 스크롤 막대가 튀지 않는다 — 높이를 canvas 에 의존시키면 놓아 주는 순간
 * 0 이 되어 문서가 접힌다.
 */
function PdfPage({
  doc,
  pageNumber,
  width,
  aspect,
  rootRef,
  onEnterView,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  width: number;
  aspect: number;
  /** 스크롤 컨테이너. 관찰자의 root 로 쓴다 — 요소가 아니라 ref 로 받아 effect 안에서 읽는다. */
  rootRef: RefObject<HTMLDivElement | null>;
  onEnterView: (pageNumber: number) => void;
}) {
  const holderRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(false);
  /** 이 페이지의 실제 세로/가로 비. 한 번 재고 나면 배율이 바뀌어도 그대로 쓴다. */
  const [ownAspect, setOwnAspect] = useState<number | null>(null);

  // 두 관찰자를 나눠 둔다: 그리기는 미리(±500px), 페이지 번호는 화면 한가운데를 지날 때만.
  useEffect(() => {
    const el = holderRef.current;
    if (!el) return;
    const root = rootRef.current;
    // 들어오면 true, 멀어지면 false — 양방향이라 canvas 가 실제로 회수된다.
    const prerender = new IntersectionObserver(
      (entries) => {
        const last = entries[entries.length - 1];
        if (last) setNear(last.isIntersecting);
      },
      { root, rootMargin: "500px 0px" },
    );
    const centered = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onEnterView(pageNumber);
      },
      { root, rootMargin: "-45% 0px -45% 0px" },
    );
    prerender.observe(el);
    centered.observe(el);
    return () => {
      prerender.disconnect();
      centered.disconnect();
    };
  }, [rootRef, pageNumber, onEnterView]);

  // 실제 렌더. 폭이 바뀌면(배율/창 크기) 같은 페이지를 다시 그린다.
  useEffect(() => {
    if (!near || !width) return;
    let cancelled = false;
    let task: RenderTask | null = null;

    void (async () => {
      try {
        const page = await doc.getPage(pageNumber);
        if (cancelled) return;
        const unit = page.getViewport({ scale: 1 });
        setOwnAspect(unit.height / unit.width);
        // 화면 표시 크기는 CSS px, 실제 그리는 크기는 거기에 DPR 을 곱한 값.
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const viewport = page.getViewport({ scale: (width / unit.width) * dpr });
        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${Math.round(viewport.height / dpr)}px`;
        task = page.render({ canvas, viewport });
        await task.promise;
      } catch (err) {
        // 배율 변경으로 취소된 렌더는 정상 흐름이라 조용히 넘긴다.
        if (!cancelled && (err as { name?: string })?.name !== "RenderingCancelledException") {
          console.error(`PDF ${pageNumber} 페이지 렌더 실패`, err);
        }
      }
    })();

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [near, width, doc, pageNumber]);

  return (
    <div
      ref={holderRef}
      data-page={pageNumber}
      className="bg-white shadow-sm"
      // canvas 가 붙어 있든 아니든 높이는 비율로 고정한다. 그려진 canvas 의 CSS 높이와 같은
      // 값이라 여백이 더 붙지 않는다.
      style={{ width, height: Math.round(width * (ownAspect ?? aspect)) }}
    >
      {near && <canvas ref={canvasRef} className="block" />}
    </div>
  );
}

function openInNewTab(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}
