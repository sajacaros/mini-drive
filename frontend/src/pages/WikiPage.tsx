/**
 * 위키 (전사 단일 위키, wiki-v2 4.2/5, Phase 7-4). 스페이스 개념이 사라지고 하나의 화면이다.
 *
 * 공유 소스 현황(상태 배지 + 등록자), index.md 카탈로그(페이지 열람), 최근 컴파일 로그, Lint 실행,
 * 잡 이력(진행 중이면 폴링)을 한 화면에서 다룬다. 위키 페이지 자체는 드라이브 파일이므로 카탈로그
 * 항목은 루트 폴더 파일과 이름을 매칭해 기존 미리보기 모달로 열고, 매칭 실패 시 드라이브 폴더
 * 탐색으로 유도한다. "위키에 공유" 체크는 드라이브(FileBrowserPage)에서 하지만, 편의를 위해
 * 소스 추가 픽커도 여기에 둔다(내 소유 항목만 선택 가능).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { errorStatus, extractErrorMessage } from "@/api/client";
import { listFiles } from "@/api/files";
import type { FileNode } from "@/api/types";
import {
  type WikiJob,
  type WikiLintResult,
  type WikiOverview,
  type WikiSource,
  addSource,
  getWiki,
  listJobs,
  removeSource,
  runLint,
} from "@/api/wiki";
import { DrivePickerModal } from "@/components/DrivePickerModal";
import { Modal } from "@/components/Modal";
import { PreviewModal } from "@/components/PreviewModal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui";
import { BookIcon, FileIcon, FolderIcon, PlusIcon, TrashIcon } from "@/components/icons";
import { downloadFile } from "@/lib/download";
import { formatDateTime } from "@/lib/format";
import { fetchFilePreview } from "@/lib/preview";
import { useAuthStore } from "@/store/auth";

type Tone = "neutral" | "success" | "warning" | "danger" | "accent";

const SOURCE_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "대기 중", tone: "neutral" },
  indexed: { label: "인덱싱됨", tone: "success" },
  stale: { label: "재인덱싱 필요", tone: "warning" },
  failed: { label: "실패", tone: "danger" },
};

const JOB_KIND: Record<string, string> = {
  ingest: "인제스트",
  compile: "컴파일",
  lint: "Lint",
};

const JOB_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "대기", tone: "neutral" },
  running: { label: "진행 중", tone: "accent" },
  done: { label: "완료", tone: "success" },
  failed: { label: "실패", tone: "danger" },
};

/** index.md 카탈로그 라인 `- [제목](href)` 파싱. */
function parseIndexEntry(line: string): { title: string; href: string } | null {
  const m = line.match(/^-\s*\[(.+?)\]\((.+?)\)/);
  if (!m) return null;
  return { title: m[1], href: m[2] };
}

/** href 의 파일명(마지막 경로 세그먼트, 디코드). */
function hrefBasename(href: string): string {
  const clean = href.split(/[?#]/)[0];
  const seg = clean.split("/").filter(Boolean).pop() ?? clean;
  try {
    return decodeURIComponent(seg);
  } catch {
    return seg;
  }
}

export function WikiPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);

  const [overview, setOverview] = useState<WikiOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [pickerOpen, setPickerOpen] = useState(false);
  const [addingId, setAddingId] = useState<number | null>(null);
  const [removeTarget, setRemoveTarget] = useState<WikiSource | null>(null);

  const [lintRunning, setLintRunning] = useState(false);
  const [lintResult, setLintResult] = useState<WikiLintResult | null>(null);

  const [jobs, setJobs] = useState<WikiJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);

  // 루트 폴더 파일 이름 → FileNode (카탈로그 항목 열람용). 개요 로드 시 채운다.
  const [pageFiles, setPageFiles] = useState<Map<string, FileNode>>(new Map());
  const [preview, setPreview] = useState<{ fileId: number; name: string } | null>(null);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ov = await getWiki();
      setOverview(ov);
      // 루트 폴더의 페이지 파일 목록(이름 매칭용). 실패해도 개요는 표시한다.
      // 백엔드 size 상한(100)에 맞춰 전체 페이지를 순회한다.
      try {
        const map = new Map<string, FileNode>();
        let page = 1;
        for (;;) {
          const res = await listFiles(ov.root_folder_id, page, 100);
          for (const f of res.items) if (!f.is_folder) map.set(f.name, f);
          if (page * 100 >= res.total || res.items.length === 0) break;
          page += 1;
        }
        setPageFiles(map);
      } catch {
        setPageFiles(new Map());
      }
    } catch (err) {
      setError(extractErrorMessage(err, "위키를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadJobs = useCallback(async () => {
    setJobsLoading(true);
    try {
      setJobs(await listJobs());
    } catch (err) {
      toast.error(extractErrorMessage(err, "잡 이력을 불러오지 못했습니다."));
    } finally {
      setJobsLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadOverview();
    void loadJobs();
  }, [loadOverview, loadJobs]);

  // 진행 중(queued/running) 잡이 있으면 5초마다 잡·개요를 갱신한다.
  const hasActiveJob = jobs.some((j) => j.status === "queued" || j.status === "running");
  const overviewRef = useRef(loadOverview);
  overviewRef.current = loadOverview;
  const jobsRef = useRef(loadJobs);
  jobsRef.current = loadJobs;
  useEffect(() => {
    if (!hasActiveJob) return;
    const timer = setInterval(() => {
      void jobsRef.current();
      void overviewRef.current();
    }, 5000);
    return () => clearInterval(timer);
  }, [hasActiveJob]);

  const handlePick = async (node: FileNode) => {
    setAddingId(node.id);
    try {
      await addSource(node.id);
      toast.success(`"${node.name}" 을(를) 위키에 공유했습니다.`);
      setPickerOpen(false);
      await loadOverview();
      await loadJobs();
    } catch (err) {
      const msg =
        errorStatus(err) === 403
          ? "소유자만 위키에 공유할 수 있습니다."
          : errorStatus(err) === 409
            ? "이미 공유된 항목입니다."
            : extractErrorMessage(err, "위키 공유에 실패했습니다.");
      toast.error(msg);
    } finally {
      setAddingId(null);
    }
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    try {
      await removeSource(removeTarget.file_id);
      toast.success("위키 공유를 해제했습니다.");
      setRemoveTarget(null);
      await loadOverview();
    } catch (err) {
      const msg =
        errorStatus(err) === 403
          ? "소유자만 공유를 해제할 수 있습니다."
          : extractErrorMessage(err, "공유 해제에 실패했습니다.");
      toast.error(msg);
    }
  };

  const handleLint = async () => {
    setLintRunning(true);
    try {
      const res = await runLint();
      setLintResult(res);
      toast.success("Lint 를 실행했습니다.");
      await loadOverview();
    } catch (err) {
      toast.error(extractErrorMessage(err, "Lint 실행에 실패했습니다."));
    } finally {
      setLintRunning(false);
    }
  };

  // 카탈로그 항목 클릭 — 루트 폴더 파일과 이름을 매칭해 미리보기, 없으면 드라이브 폴더로 이동.
  const openEntry = (entry: { title: string; href: string }) => {
    const base = hrefBasename(entry.href);
    let file = pageFiles.get(base);
    // 이름이 확장자 없이 적혔거나 제목과 어긋날 수 있어 확장자 보정도 시도한다.
    if (!file && !/\.[a-z0-9]+$/i.test(base)) file = pageFiles.get(`${base}.md`);
    if (!file) file = pageFiles.get(entry.title) ?? pageFiles.get(`${entry.title}.md`);
    if (file) {
      setPreview({ fileId: file.id, name: file.name });
      return;
    }
    if (overview) {
      // 매칭 실패: 드라이브에서 루트 폴더를 연다(공유 폴더 탐색 라우트 재사용).
      navigate(`/shared/f/${overview.root_folder_id}`, {
        state: { name: "위키 페이지" },
      });
    }
  };

  const openRootInDrive = () => {
    if (!overview) return;
    navigate(`/shared/f/${overview.root_folder_id}`, {
      state: { name: "위키 페이지" },
    });
  };

  /** 등록자 라벨 — 내 소유면 "나", 아니면 사용자 식별자. */
  const registrantLabel = (addedBy: number) =>
    addedBy === currentUserId ? "나" : `사용자 #${addedBy}`;

  if (loading) {
    return (
      <div className="flex h-screen flex-col">
        <LoadingState />
      </div>
    );
  }

  if (error || !overview) {
    return (
      <div className="flex h-screen flex-col p-6">
        <ErrorState message={error ?? "위키를 불러올 수 없습니다."} onRetry={loadOverview} />
      </div>
    );
  }

  const registeredIds = new Set(overview.sources.map((s) => s.file_id));

  return (
    <div className="flex h-screen flex-col">
      {/* 헤더 */}
      <div className="flex items-center justify-between gap-4 border-b border-token px-6 py-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold">위키</h1>
          <p className="mt-0.5 text-sm text-muted">
            드라이브에서 "위키에 공유" 체크한 문서를 근거로 LLM 이 정리한 전사 공용 위키입니다.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button className="btn btn-secondary" onClick={openRootInDrive}>
            <FolderIcon width={16} height={16} />
            드라이브에서 보기
          </button>
          <button className="btn btn-secondary" onClick={handleLint} disabled={lintRunning}>
            {lintRunning ? <Spinner className="h-4 w-4" /> : "Lint 실행"}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto flex max-w-4xl flex-col gap-6">
          {/* 공유 소스 */}
          <section className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold">공유 소스 ({overview.sources.length})</h2>
              <button
                className="btn btn-primary px-2.5 py-1.5 text-xs"
                onClick={() => setPickerOpen(true)}
              >
                <PlusIcon width={14} height={14} />
                소스 추가
              </button>
            </div>
            {overview.sources.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted">
                아직 공유된 소스가 없습니다. 드라이브에서 파일이나 폴더에 "위키에 공유"를 체크하면
                위키 페이지가 자동으로 정리됩니다.
              </p>
            ) : (
              <ul className="flex flex-col divide-y divide-[color:var(--border-color)]">
                {overview.sources.map((s) => {
                  const st = SOURCE_STATUS[s.status] ?? { label: s.status, tone: "neutral" as Tone };
                  return (
                    <li key={s.file_id} className="flex items-center gap-3 py-2.5">
                      <span className="text-muted">
                        <FileIcon width={16} height={16} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm" title={s.file_name}>
                          {s.file_name}
                        </p>
                        <p className="text-xs text-muted">
                          등록자: {registrantLabel(s.added_by)}
                          {s.last_ingested_version != null &&
                            ` · 마지막 인제스트 v${s.last_ingested_version}`}
                        </p>
                      </div>
                      <Badge tone={st.tone}>{st.label}</Badge>
                      {/* 공유 해제는 소유자만 가능(백엔드 403). 내 소유 소스에만 버튼 노출. */}
                      {s.added_by === currentUserId && (
                        <button
                          className="btn btn-ghost px-1.5 py-1 text-muted hover:text-danger"
                          onClick={() => setRemoveTarget(s)}
                          aria-label="위키 공유 해제"
                          title="위키 공유 해제"
                        >
                          <TrashIcon width={14} height={14} />
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* 카탈로그 (index.md) */}
          <section className="card p-4">
            <h2 className="mb-3 text-sm font-semibold">
              위키 카탈로그 ({overview.index_entries.length})
            </h2>
            {overview.index_entries.length === 0 ? (
              <EmptyState
                icon={<BookIcon width={32} height={32} />}
                title="아직 페이지가 없습니다"
                hint="소스를 공유하면 백그라운드에서 위키 페이지가 만들어집니다."
              />
            ) : (
              <ul className="flex flex-col gap-1">
                {overview.index_entries.map((line, i) => {
                  const entry = parseIndexEntry(line);
                  if (!entry) {
                    return (
                      <li key={i} className="px-2 py-1.5 text-sm text-muted">
                        {line.replace(/^-\s*/, "")}
                      </li>
                    );
                  }
                  return (
                    <li key={i}>
                      <button
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-[color:var(--bg-muted)]"
                        onClick={() => openEntry(entry)}
                      >
                        <span className="text-[color:var(--accent)]">
                          <BookIcon width={15} height={15} />
                        </span>
                        <span className="truncate text-sm">{entry.title}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* Lint 결과 */}
          {lintResult && (
            <section className="card p-4">
              <h2 className="mb-3 text-sm font-semibold">Lint 결과</h2>
              {lintResult.auto_fixed.length === 0 && lintResult.reports.length === 0 ? (
                <p className="text-sm text-muted">문제가 발견되지 않았습니다.</p>
              ) : (
                <div className="flex flex-col gap-3">
                  {lintResult.auto_fixed.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[color:var(--success)]">
                        자동 수정 ({lintResult.auto_fixed.length})
                      </p>
                      <ul className="flex flex-col gap-0.5 text-sm text-muted">
                        {lintResult.auto_fixed.map((line, i) => (
                          <li key={i}>· {line}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {lintResult.reports.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[color:var(--warning)]">
                        점검 리포트 ({lintResult.reports.length})
                      </p>
                      <ul className="flex flex-col gap-0.5 text-sm text-muted">
                        {lintResult.reports.map((line, i) => (
                          <li key={i}>· {line}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </section>
          )}

          {/* 최근 컴파일 로그 */}
          <section className="card p-4">
            <h2 className="mb-3 text-sm font-semibold">최근 컴파일 로그</h2>
            {overview.recent_log.length === 0 ? (
              <p className="text-sm text-muted">아직 로그가 없습니다.</p>
            ) : (
              <ul className="flex flex-col gap-0.5 font-mono text-xs text-muted">
                {overview.recent_log.map((line, i) => (
                  <li key={i} className="break-words">
                    {line.replace(/^-\s*/, "")}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* 잡 이력 */}
          <section className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold">
                잡 이력
                {hasActiveJob && (
                  <span className="ml-2 text-xs font-normal text-[color:var(--accent)]">
                    진행 중 · 자동 새로고침
                  </span>
                )}
              </h2>
              <button
                className="btn btn-secondary px-2.5 py-1.5 text-xs"
                onClick={loadJobs}
                disabled={jobsLoading}
              >
                {jobsLoading ? <Spinner className="h-4 w-4" /> : "새로고침"}
              </button>
            </div>
            {jobs.length === 0 ? (
              <p className="text-sm text-muted">잡 이력이 없습니다.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-[color:var(--border-color)]">
                {jobs.map((j) => {
                  const kind = JOB_KIND[j.kind] ?? j.kind;
                  const st = JOB_STATUS[j.status] ?? { label: j.status, tone: "neutral" as Tone };
                  return (
                    <li key={j.id} className="flex items-center gap-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm">
                          <span className="font-medium">{kind}</span>
                          {j.file_id != null && (
                            <span className="text-muted"> · 파일 #{j.file_id}</span>
                          )}
                          {j.retries > 0 && (
                            <span className="text-muted"> · 재시도 {j.retries}회</span>
                          )}
                        </p>
                        <p className="text-xs text-muted">{formatDateTime(j.updated_at)}</p>
                        {j.error && (
                          <p className="mt-0.5 break-words text-xs text-danger">{j.error}</p>
                        )}
                      </div>
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      </div>

      {/* 소스 추가 (드라이브 선택 — 내 소유 항목만 선택 가능) */}
      <DrivePickerModal
        open={pickerOpen}
        title="위키에 공유할 파일/폴더 선택"
        onClose={() => setPickerOpen(false)}
        onPick={(node) => {
          if (addingId != null) return;
          void handlePick(node);
        }}
        disabledIds={registeredIds}
        pickableOwnerId={currentUserId ?? undefined}
      />

      {/* 공유 해제 확인 */}
      <Modal
        open={removeTarget !== null}
        title="위키 공유 해제"
        onClose={() => setRemoveTarget(null)}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setRemoveTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmRemove}>
              공유 해제
            </button>
          </>
        }
      >
        <p className="text-sm">
          <span className="font-medium">{removeTarget?.file_name}</span> 의 위키 공유를 해제할까요?
        </p>
        <p className="mt-2 text-sm text-muted">
          이미 만들어진 위키 페이지는 자동으로 지워지지 않고, Lint 실행 시 정리 안내로 표시됩니다.
        </p>
      </Modal>

      {/* 위키 페이지 미리보기 */}
      <PreviewModal
        open={preview !== null}
        title={preview?.name ?? ""}
        onClose={() => setPreview(null)}
        load={() =>
          fetchFilePreview(preview!.fileId).catch((err) => {
            const s = errorStatus(err);
            if (s === 403 || s === 404) {
              const msg = "이 페이지에 접근할 수 없습니다.";
              toast.error(msg);
              throw new Error(msg);
            }
            throw new Error(extractErrorMessage(err, "미리보기를 불러오지 못했습니다."));
          })
        }
        onDownload={preview ? () => void downloadFile(preview.fileId) : undefined}
      />
    </div>
  );
}
