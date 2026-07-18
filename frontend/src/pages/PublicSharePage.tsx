import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchPublicShareMeta, ShareMetaError } from "@/api/shares";
import type { SharePublicMeta } from "@/api/types";
import { PreviewModal } from "@/components/PreviewModal";
import { DownloadIcon, DriveIcon, EyeIcon, FileIcon, LockIcon } from "@/components/icons";
import { LoadingState, Spinner } from "@/components/ui";
import { downloadSharedFile, ShareDownloadError } from "@/lib/download";
import { formatBytes, formatDateTime } from "@/lib/format";
import { fetchPublicSharePreview } from "@/lib/preview";

type ViewState =
  | { kind: "loading" }
  | { kind: "ready"; meta: SharePublicMeta }
  | { kind: "not-found" }
  | { kind: "gone"; message: string };

export function PublicSharePage() {
  const { shareUrl = "" } = useParams();
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [password, setPassword] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const meta = await fetchPublicShareMeta(shareUrl);
      setState({ kind: "ready", meta });
    } catch (err) {
      if (err instanceof ShareMetaError) {
        if (err.status === 404) setState({ kind: "not-found" });
        else if (err.status === 410) setState({ kind: "gone", message: err.message });
        else setState({ kind: "gone", message: err.message });
      } else {
        setState({ kind: "gone", message: "공유 정보를 불러오지 못했습니다." });
      }
    }
  }, [shareUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  const onDownload = async () => {
    if (state.kind !== "ready") return;
    setDownloadError(null);
    setDownloading(true);
    try {
      await downloadSharedFile(shareUrl, password || undefined);
    } catch (err) {
      if (err instanceof ShareDownloadError) {
        // 401: 비밀번호 필요/오류, 410: 만료·비활성·횟수 초과.
        if (err.status === 410) {
          setState({ kind: "gone", message: err.message });
        } else {
          setDownloadError(err.message);
        }
      } else {
        setDownloadError("다운로드에 실패했습니다.");
      }
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2 text-[color:var(--accent)]">
          <DriveIcon width={24} height={24} />
          <span className="text-lg font-semibold text-[color:var(--text-primary)]">Mini Drive</span>
        </div>

        <div className="card p-6">
          {state.kind === "loading" && <LoadingState />}

          {state.kind === "not-found" && (
            <StatusMessage
              emoji="🔍"
              title="링크를 찾을 수 없습니다"
              hint="존재하지 않는 공유 링크입니다. 주소를 다시 확인해 주세요."
            />
          )}

          {state.kind === "gone" && (
            <StatusMessage emoji="⏳" title="사용할 수 없는 링크입니다" hint={state.message} />
          )}

          {state.kind === "ready" && (
            <div className="flex flex-col gap-5">
              <div className="flex items-center gap-3">
                <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-muted-token text-muted">
                  <FileIcon width={24} height={24} />
                </span>
                <div className="min-w-0">
                  <p className="truncate font-medium">{state.meta.file_name}</p>
                  <p className="text-sm text-muted">{formatBytes(state.meta.size)}</p>
                </div>
              </div>

              {state.meta.expires_at && (
                <p className="text-xs text-muted">
                  {formatDateTime(state.meta.expires_at)}까지 유효
                </p>
              )}

              {state.meta.password_required && (
                <div>
                  <label className="label flex items-center gap-1.5">
                    <LockIcon width={14} height={14} />
                    비밀번호
                  </label>
                  <input
                    type="password"
                    className="input"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && onDownload()}
                    placeholder="공유 비밀번호를 입력하세요"
                  />
                </div>
              )}

              {downloadError && (
                <p className="rounded-lg alert-danger px-3 py-2 text-sm">{downloadError}</p>
              )}

              <div className="flex flex-col gap-2">
                <button className="btn btn-secondary" onClick={() => setPreviewOpen(true)}>
                  <EyeIcon width={16} height={16} />
                  미리보기
                </button>
                <button className="btn btn-primary" onClick={onDownload} disabled={downloading}>
                  {downloading ? (
                    <Spinner className="h-4 w-4" />
                  ) : (
                    <>
                      <DownloadIcon width={16} height={16} />
                      다운로드
                    </>
                  )}
                </button>
              </div>

              {state.meta.permission === "read" && (
                <p className="text-center text-xs text-muted">읽기 전용으로 공유된 파일입니다.</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 미리보기 모달 — 다운로드 횟수를 소모하지 않는다. 비밀번호가 필요하면 위 입력값을 사용한다. */}
      <PreviewModal
        open={previewOpen}
        title={state.kind === "ready" ? state.meta.file_name : ""}
        onClose={() => setPreviewOpen(false)}
        load={() => fetchPublicSharePreview(shareUrl, password || undefined)}
        onDownload={() => {
          setPreviewOpen(false);
          void onDownload();
        }}
      />
    </div>
  );
}

function StatusMessage({ emoji, title, hint }: { emoji: string; title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-6 text-center">
      <span className="text-4xl">{emoji}</span>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="text-sm text-muted">{hint}</p>
    </div>
  );
}
