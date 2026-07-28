/**
 * 위키 설정 모달 (spec/wiki-index.md).
 *
 * 두 축이 **독립**이라는 게 이 화면의 뼈대다:
 *   - 인덱싱 : 이 문서를 질의 대상으로 만든다. md/html 만 가능하다.
 *   - 전사 공개 : `@전사` 그룹에 read 를 준다. **모든 형식에 가능하다.**
 *
 * 그래서 PDF·pptx 에서도 이 모달이 열리고, 인덱싱 토글만 비활성 + 사유로 표시된다.
 * (끔, 공개) 조합은 "전사 공유하되 질의 대상은 아님"으로 정상이다.
 *
 * 문구는 효과 그대로 쓴다. "위키에 포함"은 색인·정리로 읽히지만 실제 효과는 전사 공개이고,
 * 그 간극이 이 기능의 가장 큰 위험이다.
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import type { FileNode, WikiState } from "@/api/types";
import { getWikiState, purgeWikiTree, setWiki } from "@/api/wiki";
import { Modal } from "./Modal";
import { useToast } from "./Toast";
import { WikiStatusBadge, wikiStatusHint } from "./WikiStatusBadge";
import { ErrorState, LoadingState, Spinner } from "./ui";

function ToggleRow({
  checked,
  disabled,
  onChange,
  title,
  description,
  busy,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  title: string;
  description: string;
  busy?: boolean;
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border border-token p-3 ${
        disabled ? "opacity-60" : "cursor-pointer"
      }`}
    >
      <input
        type="checkbox"
        className="mt-1 shrink-0"
        checked={checked}
        disabled={disabled || busy}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="min-w-0">
        <span className="flex items-center gap-2 text-sm font-medium">
          {title}
          {busy && <Spinner className="h-3 w-3" />}
        </span>
        <span className="mt-0.5 block text-xs text-muted">{description}</span>
      </span>
    </label>
  );
}

export function WikiModal({
  file,
  open,
  onClose,
  onChanged,
}: {
  file: FileNode | null;
  open: boolean;
  onClose: () => void;
  /** 목록의 배지를 갱신하도록 알린다. */
  onChanged?: (state: WikiState) => void;
}) {
  const toast = useToast();
  const [state, setState] = useState<WikiState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"index" | "public" | "purge" | null>(null);

  const load = useCallback(async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      setState(await getWikiState(file.id));
    } catch (e) {
      setError(extractErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [file]);

  useEffect(() => {
    if (open) void load();
    else setState(null);
  }, [open, load]);

  const apply = async (
    payload: { enabled?: boolean | null; public?: boolean },
    kind: "index" | "public",
  ) => {
    if (!file) return;
    setBusy(kind);
    try {
      const next = await setWiki(file.id, payload);
      setState(next);
      onChanged?.(next);
    } catch (e) {
      toast.error(extractErrorMessage(e));
      // 실패했을 때 화면이 낙관적 상태로 남지 않도록 서버 상태를 다시 읽는다.
      await load();
    } finally {
      setBusy(null);
    }
  };

  const onPurge = async () => {
    if (!file) return;
    setBusy("purge");
    try {
      await purgeWikiTree(file.id);
      toast.success("색인 데이터를 삭제했습니다.");
      await load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const isFolder = file?.is_folder ?? false;
  const scope = state?.folder_scope;
  // 상속 중이면 이 항목을 직접 켠 게 아니라 상위 폴더 설정을 따르는 중이다.
  const inherited = state != null && state.enabled && !state.explicit;

  return (
    <Modal open={open} title={`위키 설정 — ${file?.name ?? ""}`} onClose={onClose}>
      {loading && <LoadingState />}
      {!loading && error && <ErrorState message={error} onRetry={() => void load()} />}
      {!loading && !error && state && (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <WikiStatusBadge status={state.status} />
            {wikiStatusHint(state.status) && (
              <span className="text-xs text-muted">{wikiStatusHint(state.status)}</span>
            )}
          </div>

          <ToggleRow
            title="위키 인덱싱"
            description={
              state.indexable || isFolder
                ? "이 문서를 질의로 찾을 수 있게 색인합니다. 본문이 사내 AI 서버로 전송됩니다."
                : (state.reason ?? "인덱싱할 수 없는 항목입니다.")
            }
            checked={state.enabled}
            disabled={!isFolder && !state.indexable}
            busy={busy === "index"}
            onChange={(next) => void apply({ enabled: next }, "index")}
          />

          {inherited && (
            <p className="-mt-2 text-xs text-muted">
              상위 폴더 설정을 따르고 있습니다. 여기서 끄면 이 항목만 제외됩니다.
            </p>
          )}

          {isFolder && scope && (
            <div className="rounded-lg bg-muted-token p-3 text-xs">
              <p className="font-medium">
                이 폴더의 md·html {scope.target_count}개가 인덱싱됩니다.
              </p>
              <p className="mt-1 text-muted">
                {scope.skipped_by_format > 0 &&
                  `지원하지 않는 형식 ${scope.skipped_by_format}개 제외. `}
                {scope.skipped_by_size > 0 && `크기 초과 ${scope.skipped_by_size}개 제외. `}
                {scope.skipped_by_permission > 0 &&
                  `권한 부족 ${scope.skipped_by_permission}개 제외. `}
                {scope.skipped_by_optout > 0 &&
                  `소유자가 직접 끈 ${scope.skipped_by_optout}개 제외. `}
                앞으로 이 폴더에 올라오는 md·html 도 자동 포함됩니다.
              </p>
            </div>
          )}

          <ToggleRow
            title="전 구성원에게 공개"
            description="이 항목을 모든 구성원이 열람·다운로드할 수 있게 됩니다. 이전 버전도 포함됩니다."
            checked={state.public}
            busy={busy === "public"}
            onChange={(next) => void apply({ public: next }, "public")}
          />

          {state.public && (
            <p className="-mt-2 text-xs" style={{ color: "var(--warning)" }}>
              공개를 해제해도 이미 내려받은 파일은 회수되지 않습니다.
            </p>
          )}

          {state.status === "failed" && (
            <div className="rounded-lg border border-token p-3 text-xs">
              <p className="font-medium" style={{ color: "var(--danger)" }}>
                인덱싱에 실패했습니다.
              </p>
              <p className="mt-1 text-muted">
                껐다 다시 켜면 재시도합니다. 계속 실패하면 관리자에게 문의하세요.
              </p>
            </div>
          )}

          {!state.enabled && state.status !== "off" && (
            <div className="rounded-lg border border-token p-3 text-xs">
              <p className="text-muted">
                색인 데이터는 다시 켤 때를 위해 일정 기간 보관됩니다. 지금 지우려면 아래를
                누르세요.
              </p>
              <button
                className="btn btn-ghost mt-2 px-2 py-1 text-xs"
                onClick={() => void onPurge()}
                disabled={busy != null}
              >
                {busy === "purge" ? <Spinner className="h-3 w-3" /> : "색인 데이터 즉시 삭제"}
              </button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
