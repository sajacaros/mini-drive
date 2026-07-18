/**
 * 파일 썸네일 (PRD 3.2). 이미지 파일이면 인증 헤더로 blob 을 받아 objectURL 로 렌더하고,
 * 이미지가 아니거나 아직 썸네일이 없으면(404) 넘겨받은 폴백(파일 유형 아이콘)을 보여준다.
 *
 * <img src> 에는 인증 헤더를 실을 수 없어 fetch → objectURL 방식을 쓴다(기존 API 클라이언트 패턴).
 * 이미지 mime 이 아니면 아예 네트워크 요청을 하지 않아 불필요한 404 를 피한다.
 */

import { useEffect, useState } from "react";

import { fetchThumbnail } from "@/api/files";
import type { FileNode } from "@/api/types";

export function Thumbnail({
  file,
  className = "",
  fallback,
}: {
  file: FileNode;
  className?: string;
  fallback: React.ReactNode;
}) {
  const isImage = !file.is_folder && (file.mime_type?.startsWith("image/") ?? false);
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!isImage) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    void (async () => {
      try {
        const blob = await fetchThumbnail(file.id);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch {
        // 404(썸네일 미생성/이미지 아님) 등 → 폴백 유지.
        if (!cancelled) setUrl(null);
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.id, isImage]);

  if (url) {
    return <img src={url} alt="" loading="lazy" className={className} />;
  }
  return <>{fallback}</>;
}
