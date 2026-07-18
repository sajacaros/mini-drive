/** 표시용 포맷 헬퍼 (파일 크기, 날짜). 한국어 UI 기준. */

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/** 바이트를 사람이 읽는 단위로 변환한다. 예: 1536 → "1.5 KB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    UNITS.length - 1,
  );
  const value = bytes / Math.pow(1024, exponent);
  // B 단위는 소수점 없이, 그 외는 소수점 한 자리.
  const digits = exponent === 0 ? 0 : value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${UNITS[exponent]}`;
}

/** ISO 문자열을 'YYYY.MM.DD HH:mm' 로 표시한다. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

/** 날짜만 'YYYY.MM.DD'. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`;
}

/** 사용률(0~1)을 백분율 정수 문자열로. */
export function formatPercent(ratio: number): string {
  if (!Number.isFinite(ratio) || ratio <= 0) return "0%";
  return `${Math.min(100, Math.round(ratio * 100))}%`;
}
