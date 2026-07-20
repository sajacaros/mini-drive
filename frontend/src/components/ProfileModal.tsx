/**
 * 내 프로필 모달 — 아바타 변경 + 표시 이름 수정 + 비밀번호 변경. 기존 /profile 페이지의 폼
 * 로직을 이곳으로 옮겨(중복 없이) 재사용한다. 오버레이/Esc 닫기는 공용 Modal 이 처리한다.
 */

import { useRef, useState, type FormEvent } from "react";

import { errorStatus, extractErrorMessage } from "@/api/client";
import { changePassword, deleteAvatar, updateMe, uploadAvatar } from "@/api/users";
import { avatarExtension, processAvatarFile } from "@/lib/avatarImage";
import { globalRoleLabel, globalRoleTone } from "@/lib/labels";
import { useAuthStore } from "@/store/auth";
import { Avatar } from "./Avatar";
import { Modal } from "./Modal";
import { useToast } from "./Toast";
import { Badge, Spinner } from "./ui";

export function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="내 프로필" size="lg">
      <div className="flex flex-col gap-6">
        <AvatarSection />
        <div className="border-t border-token" />
        <DisplayNameSection />
        <div className="border-t border-token" />
        <PasswordSection />
      </div>
    </Modal>
  );
}

/** 아바타 미리보기 + 사진 변경 / 기본 이미지로. */
function AvatarSection() {
  const toast = useToast();
  const user = useAuthStore((s) => s.user);
  const refreshUser = useAuthStore((s) => s.refreshUser);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const label = user?.display_name || user?.email;

  const onPick = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    try {
      const blob = await processAvatarFile(file);
      await uploadAvatar(blob, `avatar.${avatarExtension(blob)}`);
      await refreshUser();
      toast.success("프로필 사진을 변경했습니다.");
    } catch (err) {
      // 429 는 client 인터셉터 공통 토스트에 위임(중복 방지). 415/413/422 는 서버 detail 노출.
      if (errorStatus(err) === 429) return;
      toast.error(extractErrorMessage(err, "프로필 사진을 변경하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async () => {
    setBusy(true);
    try {
      await deleteAvatar();
      await refreshUser();
      toast.success("프로필 사진을 기본 이미지로 변경했습니다.");
    } catch (err) {
      toast.error(extractErrorMessage(err, "프로필 사진을 삭제하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-4">
      <Avatar avatarUrl={user?.avatar_url ?? null} name={label} size={72} />
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            {busy ? <Spinner className="h-4 w-4" /> : "사진 변경"}
          </button>
          {user?.avatar_url && (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={onRemove}
              disabled={busy}
            >
              기본 이미지로
            </button>
          )}
        </div>
        <p className="text-xs text-muted">
          정사각형으로 잘라 512x512 로 저장됩니다. 원본 크기 제한은 없습니다.
        </p>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          void onPick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

/** 표시 이름 수정. */
function DisplayNameSection() {
  const toast = useToast();
  const user = useAuthStore((s) => s.user);
  const refreshUser = useAuthStore((s) => s.refreshUser);

  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [saving, setSaving] = useState(false);

  const dirty = displayName.trim() !== (user?.display_name ?? "");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = displayName.trim();
    if (!trimmed) {
      toast.error("이름을 입력하세요.");
      return;
    }
    setSaving(true);
    try {
      await updateMe(trimmed);
      await refreshUser();
      toast.success("프로필을 저장했습니다.");
    } catch (err) {
      toast.error(extractErrorMessage(err, "프로필 저장에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <div>
        <label className="label" htmlFor="displayName">
          이름
        </label>
        <input
          id="displayName"
          className="input"
          type="text"
          maxLength={100}
          placeholder="표시할 이름"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <p className="mt-1.5 text-xs text-muted">
          드라이브·그룹 등에서 다른 구성원에게 표시되는 이름입니다.
        </p>
      </div>

      <div>
        <label className="label">이메일</label>
        <div className="flex items-center gap-2 rounded-lg bg-muted-token px-3 py-2 text-sm">
          <span className="truncate">{user?.email}</span>
          {user?.role && user.role !== "user" && (
            <Badge tone={globalRoleTone(user.role)}>{globalRoleLabel(user.role)}</Badge>
          )}
        </div>
        <p className="mt-1.5 text-xs text-muted">이메일은 변경할 수 없습니다.</p>
      </div>

      <div className="flex justify-end">
        <button type="submit" className="btn btn-primary" disabled={saving || !dirty}>
          {saving ? <Spinner className="h-4 w-4" /> : "저장"}
        </button>
      </div>
    </form>
  );
}

/** 비밀번호 변경 섹션 — 현재/새/확인 입력. 성공 시 서버가 다른 기기 세션을 폐기한다. */
function PasswordSection() {
  const toast = useToast();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;
  const canSubmit =
    current.length > 0 && next.length > 0 && confirm.length > 0 && !mismatch;

  const reset = () => {
    setCurrent("");
    setNext("");
    setConfirm("");
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== confirm) {
      toast.error("새 비밀번호와 확인이 일치하지 않습니다.");
      return;
    }
    setSaving(true);
    try {
      await changePassword(current, next);
      reset();
      toast.success("비밀번호를 변경했습니다. 다른 기기에서는 다시 로그인해야 합니다.");
    } catch (err) {
      // 429 는 client 인터셉터가 공통 토스트로 처리하므로 중복 표시를 피한다.
      if (errorStatus(err) === 429) return;
      toast.error(extractErrorMessage(err, "비밀번호 변경에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h3 className="mb-3 text-base font-semibold">비밀번호 변경</h3>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor="currentPassword">
            현재 비밀번호
          </label>
          <input
            id="currentPassword"
            className="input"
            type="password"
            autoComplete="current-password"
            placeholder="현재 비밀번호"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>

        <div>
          <label className="label" htmlFor="newPassword">
            새 비밀번호
          </label>
          <input
            id="newPassword"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder="새 비밀번호"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </div>

        <div>
          <label className="label" htmlFor="confirmPassword">
            새 비밀번호 확인
          </label>
          <input
            id="confirmPassword"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder="새 비밀번호 확인"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            aria-invalid={mismatch}
          />
          {mismatch && (
            <p className="mt-1.5 text-xs text-[color:var(--danger)]">
              새 비밀번호와 확인이 일치하지 않습니다.
            </p>
          )}
        </div>

        <p className="text-xs text-muted">
          변경하면 보안을 위해 다른 기기의 세션이 모두 종료되어 다시 로그인해야 합니다.
        </p>

        <div className="flex justify-end">
          <button type="submit" className="btn btn-primary" disabled={saving || !canSubmit}>
            {saving ? <Spinner className="h-4 w-4" /> : "비밀번호 변경"}
          </button>
        </div>
      </form>
    </div>
  );
}
