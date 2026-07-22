/**
 * 투두 리포트 — 주별/월별 달성 현황. 완료율, 연속 달성(streak), 일별 추이 막대그래프,
 * 루틴별 달성률을 보여준다. 차트는 신규 의존성 없이 테마 토큰 기반 인라인 막대로 렌더한다.
 */

import { useCallback, useEffect, useState } from "react";

import { extractErrorMessage } from "@/api/client";
import { monthlyReport, weeklyReport } from "@/api/todos";
import type { TodoReport } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState, LoadingState } from "@/components/ui";
import {
  ChartIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FlameIcon,
} from "@/components/icons";
import { formatPercent } from "@/lib/format";
import {
  addDays,
  addMonths,
  formatMonthLabel,
  parseDateStr,
  todayStr,
  weekLabel,
  weekStart,
} from "@/lib/localDate";

type Tab = "weekly" | "monthly";

export function TodoReportsPage() {
  const [tab, setTab] = useState<Tab>("weekly");
  // 주별은 주의 월요일 앵커, 월별은 그 달 1일 앵커를 날짜 문자열로 관리한다.
  const [weekAnchor, setWeekAnchor] = useState(() => weekStart(todayStr()));
  const [monthAnchor, setMonthAnchor] = useState(() => todayStr().slice(0, 7) + "-01");

  const [report, setReport] = useState<TodoReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (tab === "weekly") {
        setReport(await weeklyReport(weekAnchor));
      } else {
        const d = parseDateStr(monthAnchor);
        setReport(await monthlyReport(d.getFullYear(), d.getMonth() + 1));
      }
    } catch (err) {
      setError(extractErrorMessage(err, "리포트를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [tab, weekAnchor, monthAnchor]);

  useEffect(() => {
    void load();
  }, [load]);

  const prev = () =>
    tab === "weekly"
      ? setWeekAnchor((a) => addDays(a, -7))
      : setMonthAnchor((a) => addMonths(a, -1));
  const next = () =>
    tab === "weekly"
      ? setWeekAnchor((a) => addDays(a, 7))
      : setMonthAnchor((a) => addMonths(a, 1));
  const rangeLabel = tab === "weekly" ? weekLabel(weekAnchor) : formatMonthLabel(monthAnchor);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                <span className="text-accent">
                  <ChartIcon width={18} height={18} />
                </span>
                리포트
              </h1>
              <p className="mt-0.5 text-sm text-muted">
                주별·월별 달성 현황과 루틴 성과를 확인하세요.
              </p>
            </div>
            {/* 주별/월별 탭 */}
            <div className="flex items-center rounded-lg border border-token p-0.5">
              {(["weekly", "monthly"] as const).map((t) => (
                <button
                  key={t}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    tab === t
                      ? "bg-[color:var(--bg-muted)] text-[color:var(--text-primary)]"
                      : "text-muted hover:text-[color:var(--text-primary)]"
                  }`}
                  onClick={() => setTab(t)}
                >
                  {t === "weekly" ? "주별" : "월별"}
                </button>
              ))}
            </div>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto w-full max-w-3xl">
          {/* 기간 네비게이션 */}
          <div className="mb-5 flex items-center justify-center gap-4">
            <button className="btn btn-ghost px-2" aria-label="이전" onClick={prev}>
              <ChevronLeftIcon />
            </button>
            <span className="min-w-40 text-center text-base font-semibold">{rangeLabel}</span>
            <button className="btn btn-ghost px-2" aria-label="다음" onClick={next}>
              <ChevronRightIcon />
            </button>
          </div>

          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void load()} />
          ) : report ? (
            <ReportBody report={report} tab={tab} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ReportBody({ report, tab }: { report: TodoReport; tab: Tab }) {
  const actionable = report.done + report.pending;
  return (
    <div className="flex flex-col gap-6">
      {/* 요약 카드들 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="완료율" value={formatPercent(report.completion_rate)} accent />
        <StatCard label="완료" value={`${report.done}`} sub={`/ ${actionable}`} />
        <StatCard
          label="현재 연속"
          value={`${report.current_streak}일`}
          icon={<FlameIcon width={16} height={16} />}
        />
        <StatCard label="최장 연속" value={`${report.longest_streak}일`} />
      </div>

      {/* 일별 추이 */}
      <section>
        <h2 className="mb-3 text-sm font-semibold">일별 추이</h2>
        <DailyChart report={report} compact={tab === "monthly"} />
      </section>

      {/* 루틴별 달성률 */}
      <section>
        <h2 className="mb-3 text-sm font-semibold">루틴별 달성률</h2>
        {report.routines.length === 0 ? (
          <p className="text-sm text-muted">이 기간에 기록된 루틴이 없습니다.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {report.routines.map((r) => (
              <div key={r.routine_id ?? r.title}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="truncate">{r.title}</span>
                  <span className="shrink-0 text-muted">
                    {r.done}/{r.actionable} · {formatPercent(r.rate)}
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted-token">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.round(r.rate * 100)}%`,
                      background: "var(--accent)",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  icon,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  icon?: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-1 text-xs text-muted">
        {icon}
        {label}
      </div>
      <div
        className="mt-1 text-2xl font-semibold"
        style={accent ? { color: "var(--success)" } : undefined}
      >
        {value}
        {sub && <span className="ml-1 text-sm font-normal text-muted">{sub}</span>}
      </div>
    </div>
  );
}

/** 일별 완료/미완료 막대 그래프. 막대 높이 = 항목 수, 초록 = 완료 비율. */
function DailyChart({ report, compact }: { report: TodoReport; compact: boolean }) {
  const maxTotal = Math.max(1, ...report.daily.map((d) => d.done + d.pending + d.skipped));
  return (
    <div className="card p-4">
      <div className="flex items-end gap-1" style={{ height: 120 }}>
        {report.daily.map((d) => {
          const total = d.done + d.pending + d.skipped;
          const h = (total / maxTotal) * 100;
          const donePct = total > 0 ? (d.done / total) * 100 : 0;
          const day = parseDateStr(d.date).getDate();
          return (
            <div
              key={d.date}
              className="flex flex-1 flex-col items-center justify-end gap-1"
              title={`${d.date} — 완료 ${d.done}, 미완료 ${d.pending}, 건너뜀 ${d.skipped}`}
            >
              <div
                className="flex w-full max-w-8 flex-col justify-end overflow-hidden rounded-t"
                style={{ height: `${h}%`, minHeight: total > 0 ? 4 : 0 }}
              >
                {/* 미완료(회색) 위 / 완료(초록) 아래로 쌓는다. */}
                <div style={{ height: `${100 - donePct}%`, background: "var(--bg-muted)" }} />
                <div style={{ height: `${donePct}%`, background: "var(--success)" }} />
              </div>
              {!compact && (
                <span className="text-[10px] text-muted">{day}</span>
              )}
            </div>
          );
        })}
      </div>
      {compact && (
        <div className="mt-1.5 flex justify-between text-[10px] text-muted">
          <span>{parseDateStr(report.range_start).getDate()}일</span>
          <span>{parseDateStr(report.range_end).getDate()}일</span>
        </div>
      )}
      <div className="mt-3 flex items-center gap-4 text-xs text-muted">
        <LegendDot color="var(--success)" label="완료" />
        <LegendDot color="var(--bg-muted)" label="미완료/건너뜀" />
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}
