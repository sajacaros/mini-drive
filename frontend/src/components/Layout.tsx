/** 인증 사용자 공통 레이아웃 — 사이드 네비 + 저장 용량 표시. 프로필 칩은 각 페이지 헤더(PageHeader) 우측에 표시된다. */

import { Link, NavLink, Outlet } from "react-router-dom";

import { formatBytes, formatPercent } from "@/lib/format";
import { useAuthStore } from "@/store/auth";
import { ThemePicker } from "./ThemePicker";
import {
  DriveIcon,
  HistoryIcon,
  LinkIcon,
  LockIcon,
  ShieldIcon,
  StarIcon,
  TrashIcon,
  UsersIcon,
} from "./icons";

export function Layout() {
  const { user } = useAuthStore();

  const used = user?.storage_used ?? 0;
  const max = user?.max_storage ?? 1;
  const ratio = max > 0 ? used / max : 0;

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-token bg-[color:var(--bg-secondary)]">
        <Link
          to="/"
          className="flex items-center gap-2 px-5 py-5 transition-opacity hover:opacity-80"
          aria-label="홈으로"
        >
          <span className="text-[color:var(--accent)]">
            <DriveIcon width={22} height={22} />
          </span>
          <span className="font-pixel text-lg font-semibold">Flex Drive</span>
        </Link>

        <nav className="flex flex-1 flex-col gap-1 px-3">
          <NavItem to="/" icon={<DriveIcon />} label="내 드라이브" end />
          <NavItem to="/favorites" icon={<StarIcon />} label="즐겨찾기" />
          <NavItem to="/recent" icon={<HistoryIcon />} label="최근" />
          <NavItem to="/groups" icon={<UsersIcon />} label="그룹" />
          <NavItem to="/trash" icon={<TrashIcon />} label="휴지통" />
          <NavItem to="/shares" icon={<LinkIcon />} label="공유 링크" />

          {(user?.role === "admin" || user?.role === "super_admin") && (
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
          <ThemePicker />
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
