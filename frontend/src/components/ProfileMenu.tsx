/** 우상단 프로필 칩 + 드롭다운 (프로필 모달 열기 / 로그아웃). 바깥 클릭·Esc 로 닫힘. */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuthStore } from "@/store/auth";
import { Avatar } from "./Avatar";
import { ProfileModal } from "./ProfileModal";
import { LogoutIcon, UserIcon } from "./icons";

export function ProfileMenu() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;

  const label = user.display_name || user.email;

  const onLogout = async () => {
    setOpen(false);
    await logout();
    navigate("/login", { replace: true });
  };

  const openProfile = () => {
    setOpen(false);
    setProfileOpen(true);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        className="flex max-w-[200px] items-center gap-2 rounded-full border border-token py-1 pl-1 pr-2.5 transition-colors hover:bg-[color:var(--bg-muted)]"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="내 계정"
      >
        <Avatar avatarUrl={user.avatar_url} name={label} size={28} />
        <span className="truncate text-sm font-medium">{label}</span>
      </button>

      {open && (
        <div
          role="menu"
          className="card absolute right-0 top-full z-40 mt-2 w-56 overflow-hidden p-1"
        >
          <div className="flex items-center gap-2.5 px-3 py-2.5">
            <Avatar avatarUrl={user.avatar_url} name={label} size={36} />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{label}</p>
              <p className="truncate text-xs text-muted">{user.email}</p>
            </div>
          </div>
          <div className="my-1 border-t border-token" />
          <button
            role="menuitem"
            className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--bg-muted)]"
            onClick={openProfile}
          >
            <UserIcon width={16} height={16} />
            프로필
          </button>
          <button
            role="menuitem"
            className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--bg-muted)]"
            onClick={onLogout}
          >
            <LogoutIcon width={16} height={16} />
            로그아웃
          </button>
        </div>
      )}

      <ProfileModal open={profileOpen} onClose={() => setProfileOpen(false)} />
    </div>
  );
}
