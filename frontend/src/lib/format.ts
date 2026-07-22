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

/**
 * 뒤에 붙일 "로/으로" 조사를 고른다. 받침이 없거나 받침이 ㄹ이면 "로", 그 외에는 "으로".
 *
 * 폴더 이름은 사용자가 정하므로 "내 드라이브"(받침 없음)와 "사진"(받침 ㄴ)이 같은 버튼 문구에
 * 들어온다 — 한쪽에 맞추면 다른 쪽이 반드시 틀린다. 한글 음절이 아니면(영문·숫자·기호로 끝나면)
 * 읽는 방식이 사람마다 달라 판정할 수 없으므로 "(으)로"로 물러선다.
 */
export function roParticle(word: string): string {
  const last = word.trim().slice(-1);
  const code = last.charCodeAt(0);
  // 한글 음절 영역(가~힣) 밖이면 판정 불가.
  if (Number.isNaN(code) || code < 0xac00 || code > 0xd7a3) return "(으)로";
  const jongseong = (code - 0xac00) % 28;
  // 0 = 받침 없음, 8 = 받침 ㄹ.
  return jongseong === 0 || jongseong === 8 ? "로" : "으로";
}
