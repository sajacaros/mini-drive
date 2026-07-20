/**
 * 런타임 베이스 경로 — 서브패스 배포(예: https://host/drive) 지원.
 *
 * 이미지에 경로를 굽지 않는다: 빌드 시 Vite `base` 를 플레이스홀더(`/__BASE__/`)로 구우면
 * 모든 에셋 URL과 `import.meta.env.BASE_URL` 이 그 값이 되고, 프론트 컨테이너 시작 시
 * 엔트리포인트(docker-entrypoint.d/40-base-path.sh)가 `BASE_PATH`(기본 "/") 로 일괄 치환한다.
 * 따라서 같은 이미지가 로컬("/")이든 서버("/drive/")든 재빌드 없이 동작한다.
 *
 * BASE_PATH 는 항상 슬래시로 끝난다: "/" 또는 "/drive/".
 */
export const BASE_PATH = import.meta.env.BASE_URL;

/**
 * 선행 "/" 절대경로를 현재 베이스 아래로 붙인다.
 *   withBase("/api/x")  →  "/api/x"        (base "/")
 *                       →  "/drive/api/x"  (base "/drive/")
 * axios baseURL, 무헤더 fetch, 백엔드가 돌려준 다운로드 URL, 공유 링크 등 어디서든 이걸 쓴다.
 */
export function withBase(path: string): string {
  const prefix = BASE_PATH.replace(/\/$/, ""); // "" 또는 "/drive"
  return path.startsWith("/") ? `${prefix}${path}` : `${prefix}/${path}`;
}
