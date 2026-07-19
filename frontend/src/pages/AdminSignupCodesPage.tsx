import { useCallback, useEffect, useState } from "react";

import { errorStatus, extractErrorMessage } from "@/api/client";
import { createSignupCode, listSignupCodes, updateSignupCode } from "@/api/signupCodes";
import type { SignupCode } from "@/api/types";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, LoadingState, Pagination, Spinner } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const PAGE_SIZE = 20;

type CodeStatus = "active" | "inactive" | "expired" | "exhausted";

const STATUS_LABEL: Record<CodeStatus, string> = {
  active: "활성",
  inactive: "비활성",
  expired: "만료",
  exhausted: "소진",
};

function statusTone(s: CodeStatus): "success" | "neutral" | "danger" {
  if (s === "active") return "success";
  if (s === "inactive") return "neutral";
  return "danger";
}

/** 코드의 실효 상태 — 비활성 > 만료 > 소진 > 활성 순으로 판정한다. */
function codeStatus(c: SignupCode): CodeStatus {
  if (!c.is_active) return "inactive";
  if (c.expires_at && new Date(c.expires_at).getTime() <= Date.now()) return "expired";
  if (c.max_uses != null && c.use_count >= c.max_uses) return "exhausted";
  return "active";
}

/** ISO(UTC) → datetime-local 입력값(로컬 시각). */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`;
}

/** datetime-local 입력값(로컬) → ISO. 빈 값이면 null. */
function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

/** admin — 가입 코드 관리 (발급/수정/활성화 토글, PRD 6.7). */
export function AdminSignupCodesPage() {
  const toast = useToast();
  const [codes, setCodes] = useState<SignupCode[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 발급 모달
  const [createOpen, setCreateOpen] = useState(false);
  const [newMemo, setNewMemo] = useState("");
  const [newExpires, setNewExpires] = useState("");
  const [newMaxUses, setNewMaxUses] = useState("");
  const [newCode, setNewCode] = useState("");

  // 수정 모달
  const [editTarget, setEditTarget] = useState<SignupCode | null>(null);
  const [editMemo, setEditMemo] = useState("");
  const [editExpires, setEditExpires] = useState("");
  const [editMaxUses, setEditMaxUses] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSignupCodes(page, PAGE_SIZE);
      setCodes(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(extractErrorMessage(err, "가입 코드 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setNewMemo("");
    setNewExpires("");
    setNewMaxUses("");
    setNewCode("");
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const maxUses = newMaxUses.trim() ? Number(newMaxUses) : null;
    if (maxUses != null && (!Number.isInteger(maxUses) || maxUses < 1)) {
      toast.error("최대 사용 횟수는 1 이상의 정수여야 합니다.");
      return;
    }
    setBusy(true);
    try {
      const created = await createSignupCode({
        memo: newMemo.trim(),
        expires_at: fromLocalInput(newExpires),
        max_uses: maxUses,
        code: newCode.trim() || null,
      });
      toast.success(`가입 코드 ${created.code} 를 발급했습니다.`);
      setCreateOpen(false);
      setPage(1);
      await load();
    } catch (err) {
      if (errorStatus(err) !== 429) {
        toast.error(extractErrorMessage(err, "가입 코드 발급에 실패했습니다."));
      }
    } finally {
      setBusy(false);
    }
  };

  const openEdit = (c: SignupCode) => {
    setEditTarget(c);
    setEditMemo(c.memo);
    setEditExpires(toLocalInput(c.expires_at));
    setEditMaxUses(c.max_uses != null ? String(c.max_uses) : "");
  };

  const submitEdit = async () => {
    if (!editTarget) return;
    const maxUses = editMaxUses.trim() ? Number(editMaxUses) : null;
    if (maxUses != null && (!Number.isInteger(maxUses) || maxUses < 1)) {
      toast.error("최대 사용 횟수는 1 이상의 정수여야 합니다.");
      return;
    }
    setBusy(true);
    try {
      // 폼은 "원하는 최종 상태"를 나타낸다. 빈 만료/최대사용은 명시적 null 로 보내 무기한/무제한으로 초기화한다.
      await updateSignupCode(editTarget.id, {
        memo: editMemo.trim(),
        expires_at: fromLocalInput(editExpires),
        max_uses: maxUses,
      });
      toast.success("가입 코드를 수정했습니다.");
      setEditTarget(null);
      await load();
    } catch (err) {
      if (errorStatus(err) !== 429) {
        toast.error(extractErrorMessage(err, "가입 코드 수정에 실패했습니다."));
      }
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (c: SignupCode) => {
    try {
      await updateSignupCode(c.id, { is_active: !c.is_active });
      toast.success(!c.is_active ? "활성화했습니다." : "비활성화했습니다.");
      await load();
    } catch (err) {
      if (errorStatus(err) !== 429) {
        toast.error(extractErrorMessage(err, "상태 변경에 실패했습니다."));
      }
    }
  };

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      toast.success("가입 코드를 복사했습니다.");
    } catch {
      toast.error("복사에 실패했습니다.");
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen flex-col">
      <div className="flex items-center justify-between border-b border-token px-6 py-4">
        <h1 className="text-lg font-semibold">가입 코드</h1>
        <button className="btn btn-primary" onClick={openCreate}>
          코드 발급
        </button>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} onRetry={load} />
        ) : codes.length === 0 ? (
          <EmptyState title="발급된 가입 코드가 없습니다" hint="코드를 발급하면 구성원이 가입할 수 있습니다." />
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-token text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">코드</th>
                  <th className="px-4 py-2.5 font-medium">메모</th>
                  <th className="w-32 px-4 py-2.5 font-medium">만료</th>
                  <th className="w-24 px-4 py-2.5 text-right font-medium">사용</th>
                  <th className="w-20 px-4 py-2.5 font-medium">상태</th>
                  <th className="w-32 px-4 py-2.5 font-medium">생성일</th>
                  <th className="w-48 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {codes.map((c) => {
                  const st = codeStatus(c);
                  return (
                    <tr key={c.id} className="border-b border-token last:border-0">
                      <td className="px-4 py-2.5">
                        <code className="font-medium">{c.code}</code>
                      </td>
                      <td className="px-4 py-2.5 text-muted">
                        <span className="line-clamp-1">{c.memo || "-"}</span>
                      </td>
                      <td className="px-4 py-2.5 text-muted">
                        {c.expires_at ? formatDateTime(c.expires_at) : "무기한"}
                      </td>
                      <td className="px-4 py-2.5 text-right text-muted">
                        {c.use_count}
                        {c.max_uses != null ? ` / ${c.max_uses}` : " / ∞"}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge tone={statusTone(st)}>{STATUS_LABEL[st]}</Badge>
                      </td>
                      <td className="px-4 py-2.5 text-muted">{formatDateTime(c.created_at)}</td>
                      <td className="px-4 py-2.5">
                        <div className="flex flex-wrap justify-end gap-2">
                          <button className="btn btn-ghost" onClick={() => copyCode(c.code)}>
                            복사
                          </button>
                          <button className="btn btn-secondary" onClick={() => openEdit(c)}>
                            수정
                          </button>
                          <button
                            className={c.is_active ? "btn btn-ghost text-danger" : "btn btn-secondary"}
                            onClick={() => toggleActive(c)}
                          >
                            {c.is_active ? "비활성화" : "활성화"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>

      {/* 발급 모달 */}
      <Modal
        open={createOpen}
        title="가입 코드 발급"
        onClose={() => (busy ? undefined : setCreateOpen(false))}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setCreateOpen(false)} disabled={busy}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitCreate} disabled={busy}>
              {busy ? <Spinner className="h-4 w-4" /> : "발급"}
            </button>
          </>
        }
      >
        <CodeFormFields
          memo={newMemo}
          setMemo={setNewMemo}
          expires={newExpires}
          setExpires={setNewExpires}
          maxUses={newMaxUses}
          setMaxUses={setNewMaxUses}
          code={newCode}
          setCode={setNewCode}
        />
      </Modal>

      {/* 수정 모달 */}
      <Modal
        open={editTarget !== null}
        title="가입 코드 수정"
        onClose={() => (busy ? undefined : setEditTarget(null))}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setEditTarget(null)} disabled={busy}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitEdit} disabled={busy}>
              {busy ? <Spinner className="h-4 w-4" /> : "저장"}
            </button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          <code className="font-medium">{editTarget?.code}</code> 의 메모·만료·최대 사용 횟수를 수정합니다.
        </p>
        <CodeFormFields
          memo={editMemo}
          setMemo={setEditMemo}
          expires={editExpires}
          setExpires={setEditExpires}
          maxUses={editMaxUses}
          setMaxUses={setEditMaxUses}
        />
      </Modal>
    </div>
  );
}

/** 발급/수정 공용 입력 필드. code 는 발급 시에만(setCode 존재 시) 노출한다. */
function CodeFormFields({
  memo,
  setMemo,
  expires,
  setExpires,
  maxUses,
  setMaxUses,
  code,
  setCode,
}: {
  memo: string;
  setMemo: (v: string) => void;
  expires: string;
  setExpires: (v: string) => void;
  maxUses: string;
  setMaxUses: (v: string) => void;
  code?: string;
  setCode?: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <label className="label" htmlFor="code-memo">
          메모 <span className="font-normal text-muted">(선택)</span>
        </label>
        <input
          id="code-memo"
          type="text"
          className="input"
          maxLength={200}
          placeholder="예: 2026 상반기 신규 입사자"
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
        />
      </div>
      <div>
        <label className="label" htmlFor="code-expires">
          만료 일시 <span className="font-normal text-muted">(선택 — 비우면 무기한)</span>
        </label>
        <input
          id="code-expires"
          type="datetime-local"
          className="input"
          value={expires}
          onChange={(e) => setExpires(e.target.value)}
        />
      </div>
      <div>
        <label className="label" htmlFor="code-max-uses">
          최대 사용 횟수 <span className="font-normal text-muted">(선택 — 비우면 무제한)</span>
        </label>
        <input
          id="code-max-uses"
          type="number"
          min={1}
          step={1}
          className="input"
          placeholder="무제한"
          value={maxUses}
          onChange={(e) => setMaxUses(e.target.value)}
        />
      </div>
      {setCode && (
        <div>
          <label className="label" htmlFor="code-custom">
            커스텀 코드 <span className="font-normal text-muted">(선택 — 비우면 자동 생성)</span>
          </label>
          <input
            id="code-custom"
            type="text"
            className="input"
            maxLength={64}
            placeholder="자동 생성"
            value={code ?? ""}
            onChange={(e) => setCode?.(e.target.value)}
          />
        </div>
      )}
    </div>
  );
}
