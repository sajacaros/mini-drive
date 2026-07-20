/**
 * 아바타 이미지 로더 — <img src> 는 Authorization 헤더를 못 보내므로, 인증된 apiClient 로
 * blob 을 받아 objectURL 로 변환해 캐시한다. 캐시 키는 서버가 준 avatar_url 전체이며,
 * 그 안의 ?v={epoch} 값이 바뀌면(사진 변경) 자동으로 다른 키가 되어 무효화된다.
 */

import apiClient from "@/api/client";

// avatar_url(?v= 포함) -> objectURL. 진행 중 요청은 프라미스를 공유해 중복 fetch 를 막는다.
const urlCache = new Map<string, string>();
const inflight = new Map<string, Promise<string>>();

/** avatar_url 의 버전 파라미터를 뗀 경로(무효화 시 이전 버전 정리에 사용). */
function baseKey(avatarUrl: string): string {
  const q = avatarUrl.indexOf("?");
  return q === -1 ? avatarUrl : avatarUrl.slice(0, q);
}

/** 같은 아바타의 이전 버전 objectURL 을 폐기해 메모리 누수를 막는다. */
function evictOldVersions(avatarUrl: string): void {
  const base = baseKey(avatarUrl);
  for (const [key, obj] of urlCache) {
    if (key !== avatarUrl && baseKey(key) === base) {
      URL.revokeObjectURL(obj);
      urlCache.delete(key);
    }
  }
}

/**
 * avatar_url 을 인증 fetch 하여 objectURL 을 반환한다. 404(아바타 없음) 등 실패는 throw.
 * avatar_url 은 "/api/users/{id}/avatar?v=..." 형태 — apiClient baseURL 이 이미 /api 를
 * 포함하므로 선행 /api 를 떼고 요청한다(서브패스 배포는 withBase 가 처리).
 */
export async function loadAvatarObjectURL(avatarUrl: string): Promise<string> {
  const cached = urlCache.get(avatarUrl);
  if (cached) return cached;

  const existing = inflight.get(avatarUrl);
  if (existing) return existing;

  const path = avatarUrl.replace(/^\/api(?=\/)/, "");
  const promise = apiClient
    .get<Blob>(path, { responseType: "blob" })
    .then(({ data }) => {
      const objectUrl = URL.createObjectURL(data);
      evictOldVersions(avatarUrl);
      urlCache.set(avatarUrl, objectUrl);
      return objectUrl;
    })
    .finally(() => {
      inflight.delete(avatarUrl);
    });

  inflight.set(avatarUrl, promise);
  return promise;
}
