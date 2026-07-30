/**
 * 인증 사용자 공통 레이아웃 — 사이드 네비 + 저장 용량 표시. 프로필 칩은 각 페이지 헤더(PageHeader)
 * 우측에 표시된다.
 *
 * 사이드바는 세 가지로 접힌다:
 *  - 데스크톱(md+): 240px ↔ 아이콘 레일(64px) 토글(헤더의 햄버거). 선택은 localStorage 에 남아
 *    다음 방문에도 유지된다.
 *  - 모바일(<md): 화면 밖 서랍(off-canvas). 기본은 닫힘이고 상단 바의 햄버거로 연다. 390px 화면에서
 *    240px 사이드바가 늘 자리를 차지하면 본문에 150px 밖에 남지 않아 표가 통째로 화면 밖으로 밀린다.
 *  - 섹션(드라이브/위키/할 일/관리): 라벨 클릭으로 각각 접고 펼친다. 메뉴가 계속 늘어날 것을 대비한
 *    구조로, 섹션별 선택 역시 localStorage 에 남는다. 기본은 모두 펼침.
 *
 * 높이 규약: 이 레이아웃이 h-screen 을 소유하고 각 페이지는 h-full 로 그 안을 채운다. 페이지가
 * 직접 h-screen 을 쓰면 모바일 상단 바 높이만큼 화면 밖으로 넘쳐 body 가 세로로 흔들린다.
 */

import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { formatBytes, formatPercent } from "@/lib/format";
import { todayStr } from "@/lib/localDate";
import { useAuthStore } from "@/store/auth";
import { useTodoBadgeStore } from "@/store/todoBadge";
import { ThemePicker } from "./ThemePicker";
import {
  CalendarIcon,
  ChartIcon,
  ChatIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  DriveIcon,
  FolderIcon,
  HistoryIcon,
  BookIcon,
  LinkIcon,
  ListIcon,
  LockIcon,
  MenuIcon,
  RepeatIcon,
  ShieldIcon,
  StarIcon,
  TrashIcon,
  UsersIcon,
  XIcon,
} from "./icons";

const COLLAPSE_KEY = "minidrive.nav.collapsed";
const SECTIONS_KEY = "minidrive.nav.sections";

/** localStorage 의 섹션 접힘 상태. 파싱 실패나 과거 포맷은 "모두 펼침"으로 되돌린다. */
function loadClosedSections(): Record<string, boolean> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, boolean>;
    }
  } catch {
    // ignore — 아래에서 기본값으로 떨어진다.
  }
  return {};
}

export function Layout() {
  const { user } = useAuthStore();
  const location = useLocation();

  // 데스크톱 레일 모드(접힘) — 사용자가 고른 상태를 유지한다.
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === "1",
  );
  // 모바일 서랍 — 라우팅과 함께 항상 닫힌다(열린 채로 페이지가 바뀌면 본문이 가려진다).
  const [drawerOpen, setDrawerOpen] = useState(false);
  // 닫힌 서랍은 화면 밖에 있을 뿐 DOM 에는 남아 있어, 그대로 두면 Tab 으로 보이지 않는 링크에
  // 들어가 버린다. inert 로 잠그려면 지금이 모바일 폭인지 JS 로도 알아야 한다(md=768px).
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 767.98px)").matches,
  );

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767.98px)");
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      localStorage.setItem(COLLAPSE_KEY, v ? "0" : "1");
      return !v;
    });
  };

  // 섹션별 접힘 — 값이 없는 섹션은 펼침. 나중에 섹션이 늘어도 키만 추가하면 된다.
  const [closedSections, setClosedSections] = useState<Record<string, boolean>>(loadClosedSections);
  const toggleSection = (id: string) => {
    setClosedSections((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      localStorage.setItem(SECTIONS_KEY, JSON.stringify(next));
      return next;
    });
  };

  useEffect(() => {
    setDrawerOpen(false);
  }, [location.key]);

  // "오늘 할 일" 배지 — 페이지를 오갈 때마다 가볍게 따라잡는다. /todo 는 페이지가 같은 조회를
  // 직접 수행해 reportDay 로 밀어 주므로 건너뛴다(중복 호출 + 동시 물질화 경쟁 방지).
  const { date: badgeDate, done: badgeDone, total: badgeTotal, refresh: refreshBadge } =
    useTodoBadgeStore();
  useEffect(() => {
    if (location.pathname === "/todo") return;
    void refreshBadge();
  }, [location.key, location.pathname, refreshBadge]);
  const todoBadge =
    badgeDate === todayStr() && badgeTotal > 0 ? `(${badgeDone}/${badgeTotal})` : undefined;

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const used = user?.storage_used ?? 0;
  const max = user?.max_storage ?? 1;
  const ratio = max > 0 ? used / max : 0;

  return (
    <div className="flex h-screen overflow-hidden">
      {/* 모바일 서랍 뒤 배경 — 바깥을 누르면 닫힌다. */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        aria-label="주 메뉴"
        inert={isMobile && !drawerOpen}
        className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col overflow-y-auto border-r border-token bg-[color:var(--bg-secondary)] transition-transform duration-200 md:static md:translate-x-0 md:transition-none ${
          drawerOpen ? "translate-x-0" : "-translate-x-full"
        } ${collapsed ? "md:w-16" : "md:w-60"}`}
      >
        <div
          className={`flex items-center px-5 py-5 ${
            collapsed ? "md:flex-col md:gap-3 md:px-0" : ""
          }`}
        >
          <Link
            to="/"
            className="flex min-w-0 items-center gap-2 transition-opacity hover:opacity-80"
            aria-label="홈으로"
          >
            <span className="text-accent">
              <DriveIcon width={22} height={22} />
            </span>
            <span className={`font-pixel text-lg font-semibold ${collapsed ? "md:hidden" : ""}`}>
              Flex Drive
            </span>
          </Link>
          {/* 데스크톱 햄버거 — 아이콘 레일(64px) ↔ 전체(240px) 토글. */}
          <button
            className={`btn btn-ghost hidden p-1.5 md:flex ${collapsed ? "" : "ml-auto"}`}
            onClick={toggleCollapsed}
            aria-label={collapsed ? "메뉴 펼치기" : "메뉴 접기"}
            aria-pressed={collapsed}
            title={collapsed ? "메뉴 펼치기" : "메뉴 접기"}
          >
            <MenuIcon width={18} height={18} />
          </button>
          {/* 모바일 서랍 닫기 — 데스크톱에는 없다(햄버거가 레일 토글 역할). */}
          <button
            className="btn btn-ghost ml-auto p-1.5 md:hidden"
            onClick={() => setDrawerOpen(false)}
            aria-label="메뉴 닫기"
          >
            <XIcon width={18} height={18} />
          </button>
        </div>

        <nav className={`flex flex-1 flex-col ${collapsed ? "md:px-2" : ""} px-3`}>
          <NavSection
            icon={<DriveIcon width={13} height={13} />}
            label="드라이브"
            collapsed={collapsed}
            open={!closedSections["drive"]}
            onToggle={() => toggleSection("drive")}
          >
            <NavItem to="/" icon={<FolderIcon />} label="내 드라이브" collapsed={collapsed} end />
            <NavItem to="/favorites" icon={<StarIcon />} label="즐겨찾기" collapsed={collapsed} />
            <NavItem to="/recent" icon={<HistoryIcon />} label="최근" collapsed={collapsed} />
            <NavItem to="/groups" icon={<UsersIcon />} label="그룹" collapsed={collapsed} />
            <NavItem to="/trash" icon={<TrashIcon />} label="휴지통" collapsed={collapsed} />
            <NavItem to="/shares" icon={<LinkIcon />} label="공유 링크" collapsed={collapsed} />
          </NavSection>

          {/*
            위키는 드라이브의 한 화면이 아니라 **자기 섹션**이다(spec/wiki-index.md 「프런트」).
            질의와 인덱스 관리는 보는 사람도 보는 빈도도 다르다 — 질문은 매일, 무엇이 색인됐는지는
            가끔이다. 앞으로 붙을 대화 히스토리·세션도 이 섹션 아래로 들어온다.
          */}
          <NavSection
            icon={<BookIcon width={13} height={13} />}
            label="위키"
            collapsed={collapsed}
            open={!closedSections["wiki"]}
            onToggle={() => toggleSection("wiki")}
            className="mt-4"
          >
            <NavItem to="/wiki" icon={<ChatIcon />} label="질문" collapsed={collapsed} end />
            <NavItem
              to="/wiki/catalog"
              icon={<ListIcon />}
              label="문서 카탈로그"
              collapsed={collapsed}
            />
          </NavSection>

          <NavSection
            icon={<CheckCircleIcon width={13} height={13} />}
            label="할 일"
            collapsed={collapsed}
            open={!closedSections["todo"]}
            onToggle={() => toggleSection("todo")}
            // 섹션을 접어도 오늘 진행률은 보이게 라벨 옆으로 올린다.
            badge={todoBadge}
            className="mt-4"
          >
            <NavItem
              to="/todo"
              icon={<CalendarIcon />}
              label="오늘 할 일"
              badge={todoBadge}
              collapsed={collapsed}
              end
              // 월간 보기는 같은 메뉴의 다른 화면이다 — end 매칭에서 빠지므로 직접 밝힌다.
              forceActive={location.pathname === "/todo/month"}
            />
            <NavItem to="/routines" icon={<RepeatIcon />} label="반복 루틴" collapsed={collapsed} />
            <NavItem to="/todo/reports" icon={<ChartIcon />} label="리포트" collapsed={collapsed} />
          </NavSection>

          {(user?.role === "admin" || user?.role === "super_admin") && (
            <NavSection
              icon={<ShieldIcon width={13} height={13} />}
              label="관리"
              collapsed={collapsed}
              open={!closedSections["admin"]}
              onToggle={() => toggleSection("admin")}
              className="mt-4"
            >
              <NavItem to="/admin" icon={<DriveIcon />} label="대시보드" collapsed={collapsed} end />
              <NavItem to="/admin/users" icon={<UsersIcon />} label="사용자" collapsed={collapsed} />
              <NavItem
                to="/admin/signup-codes"
                icon={<LockIcon />}
                label="가입 코드"
                collapsed={collapsed}
              />
              <NavItem to="/admin/groups" icon={<UsersIcon />} label="그룹" collapsed={collapsed} />
              <NavItem
                to="/admin/shares"
                icon={<LinkIcon />}
                label="공유 링크"
                collapsed={collapsed}
              />
              <NavItem to="/admin/audit" icon={<HistoryIcon />} label="감사 로그" collapsed={collapsed} />
            </NavSection>
          )}
        </nav>

        {/* 저장 용량 사용률 (PRD me.storage_used / max_storage) — 레일 모드에서는 막대만 남긴다. */}
        <div className={`px-5 py-4 ${collapsed ? "md:px-3" : ""}`}>
          <div
            className={`mb-1 flex items-center justify-between text-xs text-muted ${
              collapsed ? "md:hidden" : ""
            }`}
          >
            <span>저장 용량</span>
            <span>{formatPercent(ratio)}</span>
          </div>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-muted-token"
            title={`저장 용량 ${formatPercent(ratio)} (${formatBytes(used)} / ${formatBytes(max)})`}
          >
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: formatPercent(ratio),
                background: ratio > 0.9 ? "var(--danger)" : "var(--accent)",
              }}
            />
          </div>
          <p className={`mt-1.5 text-xs text-muted ${collapsed ? "md:hidden" : ""}`}>
            {formatBytes(used)} / {formatBytes(max)}
          </p>
        </div>

        {/* 레일 모드에서는 ThemePicker 가 숨어 빈 칸만 남으므로 통째로 감춘다. */}
        <div className={`border-t border-token px-4 py-3 ${collapsed ? "md:hidden" : ""}`}>
          <ThemePicker />
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        {/* 모바일 상단 바 — 서랍을 여는 유일한 입구라 항상 보인다. */}
        <div className="flex shrink-0 items-center gap-2 border-b border-token px-4 py-2.5 md:hidden">
          <button
            className="btn btn-ghost p-2"
            onClick={() => setDrawerOpen(true)}
            aria-label="메뉴 열기"
            aria-expanded={drawerOpen}
          >
            <MenuIcon width={20} height={20} />
          </button>
          <Link to="/" className="flex items-center gap-2" aria-label="홈으로">
            <span className="text-accent">
              <DriveIcon width={18} height={18} />
            </span>
            <span className="font-pixel text-base font-semibold">Flex Drive</span>
          </Link>
        </div>

        {/*
          location.key 로 페이지를 리마운트해, 네비를 누르면 **어디에 있든 그 메뉴의 루트로**
          돌아가게 한다.

          페이지들이 화면 안 위치를 URL 이 아니라 컴포넌트 state 로 들고 있어서 생기는 문제다.
          예를 들어 FileBrowserPage 의 현재 폴더(path)는 state 라, 폴더를 파고든 뒤 "내 드라이브"를
          눌러도 URL 이 그대로면 리마운트가 없어 화면이 그 폴더에 머문다(목록 페이지 번호도 같다).
          React Router 는 같은 경로로 이동해도 새 location.key 를 발급하므로, 이걸 key 로 쓰면
          "같은 메뉴 다시 클릭"이 리셋으로 이어진다.
        */}
        <div className="min-h-0 flex-1">
          <Outlet key={location.key} />
        </div>
      </main>
    </div>
  );
}

/**
 * 접을 수 있는 네비 섹션 (드라이브 / 할 일 / 관리). 라벨이 토글 버튼이고 접힘 여부는 부모가
 * localStorage 로 유지한다. 레일 모드에서는 아이콘만 남지만 토글은 그대로 동작한다.
 */
function NavSection({
  icon,
  label,
  badge,
  collapsed,
  open,
  onToggle,
  className,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  /** 섹션이 접혀 항목 배지가 안 보일 때 라벨 옆에 대신 보여줄 카운트. */
  badge?: string;
  collapsed: boolean;
  open: boolean;
  onToggle: () => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={className}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        title={label}
        className={`flex w-full items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-semibold uppercase tracking-wide text-muted transition-colors hover:bg-[color:var(--bg-muted)] hover:text-[color:var(--text-primary)] ${
          collapsed ? "md:justify-center md:px-0" : ""
        }`}
      >
        {icon}
        <span className={collapsed ? "md:sr-only" : ""}>{label}</span>
        {badge && !open && (
          <span className={`tabular-nums ${collapsed ? "md:hidden" : ""}`}>{badge}</span>
        )}
        <ChevronDownIcon
          width={14}
          height={14}
          className={`ml-auto transition-transform ${open ? "" : "-rotate-90"} ${
            collapsed ? "md:hidden" : ""
          }`}
        />
      </button>
      {open && <div className="mt-1 flex flex-col gap-1">{children}</div>}
    </div>
  );
}

function NavItem({
  to,
  icon,
  label,
  badge,
  end,
  collapsed,
  forceActive,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
  /** 라벨 오른쪽에 붙는 보조 카운트 (예: "(2/5)"). 레일 모드에서는 라벨과 함께 숨긴다. */
  badge?: string;
  end?: boolean;
  collapsed: boolean;
  /** end 매칭 밖의 하위 화면(예: /todo/month)에서도 이 메뉴를 활성으로 표시한다. */
  forceActive?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      title={label}
      className={({ isActive }) =>
        `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
          collapsed ? "md:justify-center md:px-0" : ""
        } ${
          isActive || forceActive
            ? "bg-[color:var(--bg-muted)] text-accent"
            : // 호버 시 배경만 bg-muted 로 바뀌고 글씨가 text-secondary 로 남으면 대비가
              // 3.6:1 까지 떨어진다(게임보이 라이트). .btn-ghost:hover 처럼 글씨도 같이 올린다.
              "text-[color:var(--text-secondary)] hover:bg-[color:var(--bg-muted)] hover:text-[color:var(--text-primary)]"
        }`
      }
    >
      {icon}
      {/* 레일 모드에서도 접근성 이름은 남긴다(스크린리더·테스트가 라벨로 찾는다). */}
      <span className={collapsed ? "md:sr-only" : ""}>{label}</span>
      {badge && (
        <span className={`ml-auto text-xs tabular-nums text-muted ${collapsed ? "md:hidden" : ""}`}>
          {badge}
        </span>
      )}
    </NavLink>
  );
}
