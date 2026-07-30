/**
 * 위키 설정 모달 (spec/wiki-index.md).
 *
 * **스위치는 하나다.** 켜면 색인되고 전 구성원이 열람한다. 2026-07-30 개정 전에는 `인덱싱`과
 * `전 구성원에게 공개`가 독립 스위치였는데, 실제로 색인된 467건 중 공개된 것이 2건이었다 —
 * 사람들은 인덱싱만 켜고 공개는 켜지 않았고, 그러면 남이 질의해도 아무것도 못 찾는다.
 * 둘 다 켜야 비로소 동작하는 기능은 그 이해를 요구하는 것 자체가 비용이다.
 *
 * 그래서 이 화면의 위험이 하나로 모인다 — **스위치 하나가 전사 공개를 일으킨다.** 문구는
 * 효과 그대로 쓴다. "위키에 포함"은 색인·정리로 읽히지만 실제 효과는 전사 공개이고, 축을 합친
 * 뒤로는 이 문구가 유일한 안전장치다.
 *
 * md/html 이 아닌 파일에서도 이 모달은 **열린다.** 스위치가 비활성 + 사유로 뜬다 — 숨기면
 * 사용자는 "이 기능은 없구나"로 읽지만 실제로는 "아직 이 형식은 안 되는구나"이고, 그 차이가
 * 지원 형식이 늘었을 때 재발견으로 이어진다. 그 파일을 전사 공개하려면 권한 관리 화면에서
 * `@전사` 에 read 를 준다 — 그건 위키가 아니라 권한 부여다.
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
  const [busy, setBusy] = useState<"index" | "purge" | null>(null);

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

  const apply = async (payload: { enabled: boolean | null }) => {
    if (!file) return;
    setBusy("index");
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
          {/*
            상태 배지는 **문서 한 건의 색인 상태**다. 폴더에는 트리가 없어 상태가 항상 `off` 로
            내려오는데, 스위치가 켜진 폴더에서 그걸 그리면 "위키 미포함"이 켜진 채로 보인다.
            폴더의 상태는 하위 문서들에 흩어져 있으므로 여기서 한 값으로 말할 수 없다 —
            대신 아래 범위 안내가 "몇 개가 올라가는지"를 말한다.
          */}
          {!isFolder && (
            <div className="flex items-center gap-2">
              <WikiStatusBadge status={state.status} />
              {wikiStatusHint(state.status) && (
                <span className="text-xs text-muted">{wikiStatusHint(state.status)}</span>
              )}
            </div>
          )}

          <ToggleRow
            title="전사 위키에 올리기"
            description={
              state.indexable || isFolder
                ? "모든 구성원이 질의로 찾고 열람·다운로드할 수 있게 됩니다. 본문이 사내 AI 서버로 전송됩니다."
                : (state.reason ?? "위키에 올릴 수 없는 항목입니다.")
            }
            checked={state.enabled}
            disabled={!isFolder && !state.indexable}
            busy={busy === "index"}
            onChange={(next) => void apply({ enabled: next })}
          />

          {/*
            켜기 전에 알아야 하는 것을 켜는 버튼 옆에 둔다. 이전 버전까지 열리는 것이 이제 이
            버튼의 효과다 — 별도 공개 스위치가 없어졌으므로 경고도 여기 있어야 읽힌다.
          */}
          {state.enabled ? (
            <p className="-mt-2 text-xs" style={{ color: "var(--warning)" }}>
              이전 버전까지 함께 열립니다. 내려도 이미 내려받은 파일은 회수되지 않습니다.
            </p>
          ) : (
            <p className="-mt-2 text-xs text-muted">
              켜면 이전 버전까지 전 구성원에게 열립니다.
            </p>
          )}

          {inherited && (
            <p className="-mt-1 text-xs text-muted">
              상위 폴더 설정을 따르고 있습니다. 여기서 끄면 이 항목만 제외됩니다.
            </p>
          )}

          {isFolder && scope && (
            <div className="rounded-lg bg-muted-token p-3 text-xs">
              <p className="font-medium">
                이 폴더의 md·html {scope.target_count}개가 전 구성원에게 공개됩니다.
              </p>
              <p className="mt-1 text-muted">
                {/*
                  제외되는 형식이 **공개도 되지 않는다**는 것을 같이 적는다. 폴더째로 열린다고
                  오해하면 실제보다 위험하게 읽힌다 — 공개는 대상 파일에만 걸린다.
                */}
                {scope.skipped_by_format > 0 &&
                  `지원하지 않는 형식 ${scope.skipped_by_format}개는 공개되지 않습니다. `}
                {scope.skipped_by_size > 0 && `크기 초과 ${scope.skipped_by_size}개 제외. `}
                {scope.skipped_by_permission > 0 &&
                  `권한 부족 ${scope.skipped_by_permission}개 제외. `}
                {scope.skipped_by_optout > 0 &&
                  `소유자가 직접 끈 ${scope.skipped_by_optout}개 제외. `}
                앞으로 이 폴더에 올라오는 md·html 도 자동 포함됩니다.
              </p>
            </div>
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
