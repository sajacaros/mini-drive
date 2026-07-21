/** 로컬(브라우저) 시간대 기준 날짜 유틸 — 투두/리포트는 "YYYY-MM-DD" 문자열로 다룬다.
 *
 * 백엔드의 '오늘' 기준은 KST 이고, 국내 사용자의 브라우저 로컬 시간대도 KST 이므로 로컬
 * 날짜 계산이 서버와 일치한다. UTC 파싱을 피하려 Date 를 로컬 생성자로만 만든다.
 */

const WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"];

export function toDateStr(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function parseDateStr(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function todayStr(): string {
  return toDateStr(new Date());
}

export function addDays(s: string, n: number): string {
  const d = parseDateStr(s);
  d.setDate(d.getDate() + n);
  return toDateStr(d);
}

export function addMonths(s: string, n: number): string {
  const d = parseDateStr(s);
  d.setMonth(d.getMonth() + n);
  return toDateStr(d);
}

/** ISO 요일(월=0..일=6) — Date.getDay()(일=0) 를 변환. */
export function isoWeekday(d: Date): number {
  return (d.getDay() + 6) % 7;
}

/** "7월 21일 (월)" 형태. */
export function formatDayLabel(s: string): string {
  const d = parseDateStr(s);
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAY_KO[isoWeekday(d)]})`;
}

/** "2026년 7월" 형태. */
export function formatMonthLabel(s: string): string {
  const d = parseDateStr(s);
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월`;
}

/** s 가 속한 주(월요일 시작)의 월요일 날짜 문자열. */
export function weekStart(s: string): string {
  const d = parseDateStr(s);
  return addDays(s, -isoWeekday(d));
}

export function weekLabel(s: string): string {
  const start = weekStart(s);
  const end = addDays(start, 6);
  const sd = parseDateStr(start);
  const ed = parseDateStr(end);
  return `${sd.getMonth() + 1}.${sd.getDate()} ~ ${ed.getMonth() + 1}.${ed.getDate()}`;
}

export const WEEKDAY_LABELS = WEEKDAY_KO;
