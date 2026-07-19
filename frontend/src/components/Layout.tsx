/** 인증 사용자 공통 레이아웃 — 사이드 네비 + 저장 용량 표시 + 상단 바. */

import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { formatBytes, formatPercent } from "@/lib/format";
import { useAuthStore } from "@/store/auth";
import { ThemePicker } from "./ThemePicker";
import {
  DriveIcon,
  HistoryIcon,
  InboxIcon,
  LinkIcon,
  LockIcon,
  LogoutIcon,
  ShieldIcon,
  TrashIcon,
  UsersIcon,
} from "./icons";

export function Layout() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();

  const onLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const used = user?.storage_used ?? 0;
  const max = user?.max_storage ?? 1;
  const ratio = max > 0 ? used / max : 0;

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-token bg-[color:var(--bg-secondary)]">
        <div className="flex items-center gap-2 px-5 py-5">
          <span className="text-[color:var(--accent)]">
            <DriveIcon width={22} height={22} />
          </span>
          <span className="font-pixel text-lg font-semibold">Mini Drive</span>
        </div>

        <nav className="flex flex-1 flex-col gap-1 px-3">
          <NavItem to="/" icon={<DriveIcon />} label="내 드라이브" end />
          <NavItem to="/shared" icon={<InboxIcon />} label="공유됨" />
          <NavItem to="/groups" icon={<UsersIcon />} label="그룹" />
          <NavItem to="/trash" icon={<TrashIcon />} label="휴지통" />
          <NavItem to="/shares" icon={<LinkIcon />} label="공유 링크" />

          {user?.role === "admin" && (
            <div className="mt-4">
              <div className="flex items-center gap-1.5 px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                <ShieldIcon width={13} height={13} />
                관리
              </div>
              <NavItem to="/admin" icon={<DriveIcon />} label="대시보드" end />
              <NavItem to="/admin/users" icon={<UsersIcon />} label="사용자" />
              <NavItem to="/admin/signup-codes" icon={<LockIcon />} label="가입 코드" />
              <NavItem to="/admin/groups" icon={<UsersIcon />} label="그룹" />
              <NavItem to="/admin/shares" icon={<LinkIcon />} label="공유 링크" />
              <NavItem to="/admin/audit" icon={<HistoryIcon />} label="감사 로그" />
            </div>
          )}
        </nav>

        {/* 저장 용량 사용률 (PRD me.storage_used / max_storage) */}
        <div className="px-5 py-4">
          <div className="mb-1 flex items-center justify-between text-xs text-muted">
            <span>저장 용량</span>
            <span>{formatPercent(ratio)}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted-token">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: formatPercent(ratio),
                background: ratio > 0.9 ? "var(--danger)" : "var(--accent)",
              }}
            />
          </div>
          <p className="mt-1.5 text-xs text-muted">
            {formatBytes(used)} / {formatBytes(max)}
          </p>
        </div>

        <div className="border-t border-token px-4 py-3">
          <div className="mb-3">
            <ThemePicker />
          </div>
          <div className="mb-2 px-1">
            <p className="truncate text-sm font-medium">{user?.display_name || user?.email}</p>
            <p className="truncate text-xs text-muted">{user?.email}</p>
          </div>
          <button className="btn btn-ghost w-full justify-start" onClick={onLogout}>
            <LogoutIcon width={16} height={16} />
            로그아웃
          </button>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  );
}

function NavItem({
  to,
  icon,
  label,
  end,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
  end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
          isActive
            ? "bg-[color:var(--bg-muted)] text-[color:var(--accent)]"
            : "text-[color:var(--text-secondary)] hover:bg-[color:var(--bg-muted)]"
        }`
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}
