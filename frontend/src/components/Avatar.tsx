/**
 * 공용 아바타 — 사진(avatar_url)이 있으면 인증 fetch 로 로드한 이미지를, 없거나 로드 실패
 * (404 등)면 사람 실루엣 기본 아이콘을 원형으로 표시한다. 프로필 칩·프로필 모달 등 아바타가
 * 보이는 모든 곳에서 이 컴포넌트를 쓴다.
 */

import { useEffect, useState } from "react";

import { loadAvatarObjectURL } from "@/lib/avatarCache";
import { UserIcon } from "./icons";

export function Avatar({
  avatarUrl,
  name,
  size = 28,
  className = "",
}: {
  avatarUrl: string | null;
  name?: string;
  /** 지름(px). */
  size?: number;
  className?: string;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setObjectUrl(null);
    if (!avatarUrl) return;
    loadAvatarObjectURL(avatarUrl)
      .then((url) => {
        if (!cancelled) setObjectUrl(url);
      })
      .catch(() => {
        // null/404/네트워크 실패 → 기본 아이콘 유지.
      });
    return () => {
      cancelled = true;
    };
  }, [avatarUrl]);

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted-token text-muted ${className}`}
      style={{ width: size, height: size }}
      aria-hidden={name ? undefined : true}
      title={name}
    >
      {objectUrl ? (
        <img
          src={objectUrl}
          alt={name ? `${name} 프로필 사진` : ""}
          className="h-full w-full object-cover"
        />
      ) : (
        <UserIcon width={Math.round(size * 0.62)} height={Math.round(size * 0.62)} />
      )}
    </span>
  );
}
