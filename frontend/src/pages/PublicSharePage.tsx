import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchPublicShareListing, fetchPublicShareMeta, ShareMetaError } from "@/api/shares";
import type { ShareFolderListing, SharePublicEntry, SharePublicMeta } from "@/api/types";
import { PreviewModal } from "@/components/PreviewModal";
import {
  DownloadIcon,
  DriveIcon,
  EyeIcon,
  FileIcon,
  FolderIcon,
  LockIcon,
} from "@/components/icons";
import { LoadingState, Spinner } from "@/components/ui";
import { downloadSharedChild, downloadSharedFile, ShareDownloadError } from "@/lib/download";
import { formatBytes, formatDateTime } from "@/lib/format";
import { fetchPublicShareChildPreview, fetchPublicSharePreview } from "@/lib/preview";

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

  // 폴더 공유 웹 탐색 상태 — listing 이 null 이면 아직 안 열었다(비밀번호 대기 포함).
  const [listing, setListing] = useState<ShareFolderListing | null>(null);
  const [listingLoading, setListingLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [childPreview, setChildPreview] = useState<SharePublicEntry | null>(null);

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

  /** 폴더 목록을 연다(탐색 이동 포함). 401 은 비밀번호 안내, 410 은 링크 만료 화면으로. */
  const openFolder = useCallback(
    async (folderId?: number) => {
      setListingLoading(true);
      setListError(null);
      try {
        const result = await fetchPublicShareListing(shareUrl, folderId, password || undefined);
        setListing(result);
      } catch (err) {
        if (err instanceof ShareMetaError && err.status === 410) {
          setState({ kind: "gone", message: err.message });
        } else if (err instanceof ShareMetaError) {
          setListError(err.message);
        } else {
          setListError("폴더 목록을 불러오지 못했습니다.");
        }
      } finally {
        setListingLoading(false);
      }
    },
    [shareUrl, password],
  );

  // 비밀번호 없는 폴더 공유는 바로 루트 목록을 연다. (비밀번호 공유는 입력 후 버튼으로.)
  useEffect(() => {
    if (
      state.kind === "ready" &&
      state.meta.is_folder &&
      !state.meta.password_required &&
      listing === null &&
      !listingLoading
    ) {
      void openFolder();
    }
  }, [state, listing, listingLoading, openFolder]);

  const handleDownloadError = (err: unknown) => {
    if (err instanceof ShareDownloadError) {
      // 401: 비밀번호 필요/오류, 410: 만료·비활성·횟수 초과.
      if (err.status === 410) setState({ kind: "gone", message: err.message });
      else setDownloadError(err.message);
    } else {
      setDownloadError("다운로드에 실패했습니다.");
    }
  };

  /** 공유 전체 다운로드 — 파일은 원본, 폴더는 전체 ZIP. */
  const onDownload = async () => {
    if (state.kind !== "ready") return;
    setDownloadError(null);
    setDownloading(true);
    try {
      await downloadSharedFile(shareUrl, password || undefined);
    } catch (err) {
      handleDownloadError(err);
    } finally {
      setDownloading(false);
    }
  };

  /** 폴더 공유 안의 항목 하나 다운로드 — 파일은 원본, 하위 폴더는 ZIP. */
  const onChildDownload = async (entry: SharePublicEntry) => {
    setDownloadError(null);
    try {
      await downloadSharedChild(shareUrl, entry.id, password || undefined);
    } catch (err) {
      handleDownloadError(err);
    }
  };

  const isFolderBrowser = state.kind === "ready" && state.meta.is_folder && listing !== null;

  // 읽기 전용 공유는 다운로드 동선을 걷어낸다 — 백엔드도 403 으로 막으므로, 버튼을 남기면
  // 누르는 족족 오류만 보게 된다. 미리보기(열람)는 read 가 주는 것 그 자체라 그대로 둔다.
  const readOnly = state.kind === "ready" && state.meta.permission === "read";

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className={`w-full ${isFolderBrowser ? "max-w-2xl" : "max-w-md"}`}>
        <div className="mb-6 flex items-center justify-center gap-2 text-accent">
          <DriveIcon width={24} height={24} />
          <span className="text-lg font-semibold text-[color:var(--text-primary)]">Flex Drive</span>
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

          {/* --- 단일 파일 공유 --------------------------------------------- */}
          {state.kind === "ready" && !state.meta.is_folder && (
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
                <PasswordField
                  value={password}
                  onChange={setPassword}
                  // 읽기 전용이면 Enter 가 갈 곳은 미리보기뿐이다.
                  onEnter={() => (readOnly ? setPreviewOpen(true) : void onDownload())}
                />
              )}

              {downloadError && (
                <p className="rounded-lg alert-danger px-3 py-2 text-sm">{downloadError}</p>
              )}

              <div className="flex flex-col gap-2">
                {/* 읽기 전용이면 미리보기가 유일한 동작이므로 주 버튼 자리를 물려받는다. */}
                <button
                  className={readOnly ? "btn btn-primary" : "btn btn-secondary"}
                  onClick={() => setPreviewOpen(true)}
                >
                  <EyeIcon width={16} height={16} />
                  미리보기
                </button>
                {!readOnly && (
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
                )}
              </div>

              {readOnly && (
                <p className="text-center text-xs text-muted">
                  읽기 전용으로 공유된 파일입니다. 미리보기만 할 수 있습니다.
                </p>
              )}
            </div>
          )}

          {/* --- 폴더 공유: 비밀번호 잠금 화면 ------------------------------- */}
          {state.kind === "ready" && state.meta.is_folder && listing === null && (
            <div className="flex flex-col gap-5">
              <div className="flex items-center gap-3">
                <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-muted-token text-muted">
                  <FolderIcon width={24} height={24} />
                </span>
                <div className="min-w-0">
                  <p className="truncate font-medium">{state.meta.file_name}</p>
                  <p className="text-sm text-muted">공유 폴더</p>
                </div>
              </div>

              {state.meta.expires_at && (
                <p className="text-xs text-muted">
                  {formatDateTime(state.meta.expires_at)}까지 유효
                </p>
              )}

              {state.meta.password_required ? (
                <>
                  <PasswordField
                    value={password}
                    onChange={setPassword}
                    onEnter={() => void openFolder()}
                  />
                  {listError && (
                    <p className="rounded-lg alert-danger px-3 py-2 text-sm">{listError}</p>
                  )}
                  <button
                    className="btn btn-primary"
                    onClick={() => void openFolder()}
                    disabled={listingLoading}
                  >
                    {listingLoading ? <Spinner className="h-4 w-4" /> : "폴더 열기"}
                  </button>
                </>
              ) : listError ? (
                <p className="rounded-lg alert-danger px-3 py-2 text-sm">{listError}</p>
              ) : (
                <LoadingState />
              )}
            </div>
          )}

          {/* --- 폴더 공유: 웹 탐색 ------------------------------------------ */}
          {state.kind === "ready" && state.meta.is_folder && listing !== null && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted-token text-muted">
                    <FolderIcon width={20} height={20} />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate font-medium">{listing.folder.name}</p>
                    {state.meta.expires_at && (
                      <p className="text-xs text-muted">
                        {formatDateTime(state.meta.expires_at)}까지 유효
                      </p>
                    )}
                  </div>
                </div>
                {!readOnly && (
                  <button
                    className="btn btn-primary shrink-0"
                    onClick={onDownload}
                    disabled={downloading}
                  >
                    {downloading ? (
                      <Spinner className="h-4 w-4" />
                    ) : (
                      <>
                        <DownloadIcon width={16} height={16} />
                        전체 ZIP
                      </>
                    )}
                  </button>
                )}
              </div>

              {/* 브레드크럼 — 공유 루트가 시작점이고, 그 위로는 올라갈 수 없다. */}
              <nav className="flex flex-wrap items-center gap-1 text-sm text-muted">
                {listing.breadcrumbs.map((crumb, i) => {
                  const last = i === listing.breadcrumbs.length - 1;
                  return (
                    <span key={crumb.id} className="flex items-center gap-1">
                      {i > 0 && <span aria-hidden>/</span>}
                      {last ? (
                        <span className="font-medium text-[color:var(--text-primary)]">
                          {crumb.name}
                        </span>
                      ) : (
                        <button
                          className="hover:underline"
                          onClick={() => void openFolder(crumb.id)}
                        >
                          {crumb.name}
                        </button>
                      )}
                    </span>
                  );
                })}
              </nav>

              {(downloadError || listError) && (
                <p className="rounded-lg alert-danger px-3 py-2 text-sm">
                  {downloadError ?? listError}
                </p>
              )}

              {listingLoading ? (
                <LoadingState />
              ) : listing.entries.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted">빈 폴더입니다.</p>
              ) : (
                <ul className="divide-y divide-token">
                  {listing.entries.map((entry) => (
                    <li key={entry.id} className="flex items-center gap-3 py-2">
                      <span className="shrink-0 text-muted">
                        {entry.is_folder ? (
                          <FolderIcon width={18} height={18} />
                        ) : (
                          <FileIcon width={18} height={18} />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        {entry.is_folder ? (
                          <button
                            className="block max-w-full truncate text-left font-medium hover:underline"
                            onClick={() => void openFolder(entry.id)}
                          >
                            {entry.name}
                          </button>
                        ) : (
                          <p className="truncate font-medium">{entry.name}</p>
                        )}
                        <p className="text-xs text-muted">
                          {entry.is_folder ? "폴더" : formatBytes(entry.size)} ·{" "}
                          {formatDateTime(entry.updated_at)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {!entry.is_folder && (
                          <button
                            className="btn btn-ghost"
                            title="미리보기"
                            aria-label={`${entry.name} 미리보기`}
                            onClick={() => setChildPreview(entry)}
                          >
                            <EyeIcon width={16} height={16} />
                          </button>
                        )}
                        {!readOnly && (
                          <button
                            className="btn btn-ghost"
                            title={entry.is_folder ? "ZIP 으로 다운로드" : "다운로드"}
                            aria-label={`${entry.name} 다운로드`}
                            onClick={() => void onChildDownload(entry)}
                          >
                            <DownloadIcon width={16} height={16} />
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}

              {readOnly && (
                <p className="text-center text-xs text-muted">
                  읽기 전용으로 공유된 폴더입니다. 미리보기만 할 수 있습니다.
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 단일 파일 공유 미리보기 — 다운로드 횟수를 소모하지 않는다. */}
      <PreviewModal
        open={previewOpen}
        title={state.kind === "ready" ? state.meta.file_name : ""}
        onClose={() => setPreviewOpen(false)}
        load={() => fetchPublicSharePreview(shareUrl, password || undefined)}
        // 읽기 전용이면 onDownload 를 넘기지 않는다 — "미리보기 미지원 → 다운로드" 폴백
        // 버튼까지 함께 사라진다.
        onDownload={
          readOnly
            ? undefined
            : () => {
                setPreviewOpen(false);
                void onDownload();
              }
        }
      />

      {/* 폴더 공유 안의 파일 미리보기 — 같은 규약, 대상만 하위 파일이다. */}
      <PreviewModal
        open={childPreview !== null}
        title={childPreview?.name ?? ""}
        onClose={() => setChildPreview(null)}
        load={() =>
          childPreview
            ? fetchPublicShareChildPreview(shareUrl, childPreview.id, password || undefined)
            : Promise.reject(new Error("no target"))
        }
        onDownload={
          readOnly
            ? undefined
            : () => {
                if (childPreview) void onChildDownload(childPreview);
                setChildPreview(null);
              }
        }
      />
    </div>
  );
}

function PasswordField({
  value,
  onChange,
  onEnter,
}: {
  value: string;
  onChange: (v: string) => void;
  onEnter: () => void;
}) {
  return (
    <div>
      <label className="label flex items-center gap-1.5">
        <LockIcon width={14} height={14} />
        비밀번호
      </label>
      <input
        type="password"
        className="input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onEnter()}
        placeholder="공유 비밀번호를 입력하세요"
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
