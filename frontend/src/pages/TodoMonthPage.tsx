/**
 * 월간 할 일 — 한 달을 달력 격자로 펼쳐 날짜마다 (완료/전체) 개수를 보여준다.
 *
 * 데이터는 월별 리포트 API(순수 조회 — 물질화 부작용 없음)를 그대로 쓰므로, 실제로 열어본
 * (또는 항목이 만들어진) 날의 기록만 개수가 잡히고 미래 날짜는 빈 칸으로 남는다.
 * 날짜를 누르면 그날의 일간 화면(/todo?date=...)으로 이동한다.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "@/api/client";
import { monthlyReport } from "@/api/todos";
import type { TodoReport } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { ErrorState, LoadingState } from "@/components/ui";
import {
  CalendarIcon,
  ChartIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  RepeatIcon,
} from "@/components/icons";
import {
  WEEKDAY_LABELS,
  addMonths,
  formatMonthLabel,
  isoWeekday,
  parseDateStr,
  todayStr,
} from "@/lib/localDate";

/** 오늘이 속한 달의 1일 앵커("YYYY-MM-01") — 리포트 페이지와 같은 방식. */
const currentMonthAnchor = () => todayStr().slice(0, 7) + "-01";

export function TodoMonthPage() {
  const navigate = useNavigate();

  const [monthAnchor, setMonthAnchor] = useState(currentMonthAnchor);
  const [report, setReport] = useState<TodoReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = parseDateStr(monthAnchor);
      setReport(await monthlyReport(d.getFullYear(), d.getMonth() + 1));
    } catch (err) {
      setError(extractErrorMessage(err, "월간 현황을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [monthAnchor]);

  useEffect(() => {
    void load();
  }, [load]);

  const isCurrentMonth = monthAnchor === currentMonthAnchor();
  const ratio = report?.completion_rate ?? 0;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-6 py-4">
        <PageHeader>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold">
                <span className="text-accent">
                  <CalendarIcon width={18} height={18} />
                </span>
                월간 할 일
              </h1>
              <p className="mt-0.5 text-sm text-muted">
                한 달의 할 일 개수와 달성 현황을 한눈에 보세요.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn btn-secondary" onClick={() => navigate("/todo")}>
                <CalendarIcon width={16} height={16} />
                일간
              </button>
              <button className="btn btn-secondary" onClick={() => navigate("/routines")}>
                <RepeatIcon width={16} height={16} />
                루틴
              </button>
              <button className="btn btn-secondary" onClick={() => navigate("/todo/reports")}>
                <ChartIcon width={16} height={16} />
                리포트
              </button>
            </div>
          </div>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto w-full max-w-3xl">
          {/* 달 네비게이션 */}
          <div className="mb-4 flex items-center justify-between gap-3">
            <button
              className="btn btn-ghost px-2"
              aria-label="이전 달"
              onClick={() => setMonthAnchor((a) => addMonths(a, -1))}
            >
              <ChevronLeftIcon />
            </button>
            <div className="text-center">
              <div className="text-base font-semibold">{formatMonthLabel(monthAnchor)}</div>
              {!isCurrentMonth && (
                <button
                  className="text-xs text-accent hover:underline"
                  onClick={() => setMonthAnchor(currentMonthAnchor())}
                >
                  이번 달로 이동
                </button>
              )}
            </div>
            <button
              className="btn btn-ghost px-2"
              aria-label="다음 달"
              onClick={() => setMonthAnchor((a) => addMonths(a, 1))}
            >
              <ChevronRightIcon />
            </button>
          </div>

          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void load()} />
          ) : report ? (
            <>
              {/* 월 진행률 — 분모는 그 달의 전체 항목(완료+실패+빈칸). */}
              <div className="card mb-4 p-4">
                <div className="mb-1.5 flex items-center justify-between text-sm">
                  <span className="font-medium">월 진행률</span>
                  <span className="text-muted">
                    {report.done} / {report.total} 완료
                    {report.failed > 0 && ` · ${report.failed} 실패`}
                  </span>
                </div>
                <div className="h-2.5 w-full overflow-hidden rounded-full bg-muted-token">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.round(ratio * 100)}%`,
                      background: "var(--success)",
                    }}
                  />
                </div>
              </div>

              <MonthGrid
                report={report}
                onPick={(d) => navigate(`/todo?date=${d}`)}
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** 달력 격자 — 월요일 시작 7열. 각 칸은 일자와 그날의 (완료/전체) 개수를 보여준다. */
function MonthGrid({
  report,
  onPick,
}: {
  report: TodoReport;
  onPick: (date: string) => void;
}) {
  const today = todayStr();
  // 1일이 시작하는 요일만큼 앞을 비운다(월=0 시작).
  const lead = isoWeekday(parseDateStr(report.range_start));

  return (
    <div className="card p-4">
      <div className="grid grid-cols-7 gap-1.5">
        {WEEKDAY_LABELS.map((w) => (
          <div key={w} className="pb-1 text-center text-xs font-semibold text-muted">
            {w}
          </div>
        ))}
        {Array.from({ length: lead }, (_, i) => (
          <div key={`blank-${i}`} />
        ))}
        {report.daily.map((d) => {
          const cellDate = parseDateStr(d.date);
          const dayNum = cellDate.getDate();
          const isToday = d.date === today;
          // "YYYY-MM-DD" 는 사전순 = 시간순이라 문자열 비교로 미래를 가른다.
          const isFuture = d.date > today;
          const allDone = d.total > 0 && d.done === d.total;
          return (
            <button
              key={d.date}
              onClick={() => onPick(d.date)}
              aria-label={`${cellDate.getMonth() + 1}월 ${dayNum}일${
                d.total > 0 ? ` — 완료 ${d.done} / 전체 ${d.total}` : ""
              }`}
              title={
                d.total > 0
                  ? `${d.date} — 완료 ${d.done}, 실패 ${d.failed}, 빈칸 ${d.pending}`
                  : d.date
              }
              className={`flex min-h-14 flex-col items-center justify-start gap-0.5 rounded-lg border p-1.5 transition-colors hover:bg-[color:var(--bg-muted)] ${
                isToday ? "border-[color:var(--accent)]" : "border-transparent"
              } ${isFuture ? "opacity-50" : ""}`}
            >
              <span className={`text-xs ${isToday ? "font-bold text-accent" : "text-muted"}`}>
                {dayNum}
              </span>
              {d.total > 0 && (
                <span
                  className="text-xs font-medium tabular-nums"
                  style={{
                    // 전부 완료면 초록, 실패가 섞였으면 빨강으로 하루의 결이 보이게 한다.
                    color: allDone
                      ? "var(--success)"
                      : d.failed > 0
                        ? "var(--danger)"
                        : undefined,
                  }}
                >
                  {d.done}/{d.total}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
