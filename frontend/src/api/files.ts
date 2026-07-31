/** 파일/폴더 API (PRD 6.2). */

import type { AxiosProgressEvent } from "axios";

import apiClient from "./client";
import type {
  BatchUploadResponse,
  BreadcrumbResponse,
  DownloadTicketResponse,
  FileListResponse,
  FileNode,
  FileVersionListResponse,
  ResumablePart,
  ResumableSession,
} from "./types";

export async function listFiles(
  parentId: number | null,
  page = 1,
  size = 50,
  foldersOnly = false,
): Promise<FileListResponse> {
  const { data } = await apiClient.get<FileListResponse>("/files", {
    params: {
      parentId: parentId ?? undefined,
      page,
      size,
      foldersOnly: foldersOnly || undefined,
    },
  });
  return data;
}

export async function getFile(id: number): Promise<FileNode> {
  const { data } = await apiClient.get<FileNode>(`/files/${id}`);
  return data;
}

/**
 * 폴더 URL(/f/:id)로 바로 들어왔을 때 breadcrumb 를 복원한다.
 * 앱 안에서 이동할 때는 history state 로 경로가 따라오므로 호출하지 않는다 — 새로고침과
 * 링크 진입만 서버에 묻는다.
 */
export async function getBreadcrumb(id: number): Promise<BreadcrumbResponse> {
  const { data } = await apiClient.get<BreadcrumbResponse>(`/files/${id}/breadcrumb`);
  return data;
}

export async function uploadFile(
  file: File,
  parentId: number | null,
  onProgress?: (percent: number) => void,
): Promise<FileNode> {
  const form = new FormData();
  form.append("file", file);
  if (parentId != null) form.append("parent_id", String(parentId));

  const { data } = await apiClient.post<FileNode>("/files/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (event: AxiosProgressEvent) => {
      if (onProgress && event.total) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    },
  });
  return data;
}

/**
 * 배치 업로드 — 여러 파일을 각자의 상대 경로대로 한 요청에 올린다(폴더 업로드).
 *
 * `entries` 를 비우고 `dirs` 만 보내면 폴더 트리만 만들고 `folders` 맵을 받아온다.
 * 부분 실패가 정상 경로라 HTTP 는 200 이고 성패는 항목별 `items` 가 말한다.
 */
export async function batchUpload(
  entries: { path: string; file: File }[],
  dirs: string[],
  parentId: number | null,
  onProgress?: (loaded: number) => void,
): Promise<BatchUploadResponse> {
  const form = new FormData();
  for (const { path, file } of entries) {
    // 파일명은 서버가 paths 의 마지막 세그먼트로 덮어쓰므로 여기서는 참고값이다.
    form.append("files", file, file.name);
    form.append("paths", path);
  }
  for (const dir of dirs) form.append("dirs", dir);
  if (parentId != null) form.append("parent_id", String(parentId));

  const { data } = await apiClient.post<BatchUploadResponse>("/files/batch", form, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (event: AxiosProgressEvent) => onProgress?.(event.loaded),
  });
  return data;
}

/**
 * 재업로드 = 새 버전 생성 (PRD 3.3). baseVersion 을 전달하면 현재 버전과 다를 때 409(충돌)로
 * 낙관적 잠금이 걸린다. 생략(undefined) 시 충돌 검사 없이 강제 덮어쓰기.
 */
export async function reuploadFile(
  fileId: number,
  file: File,
  baseVersion?: number,
  onProgress?: (percent: number) => void,
): Promise<FileNode> {
  const form = new FormData();
  form.append("file", file);
  if (baseVersion != null) form.append("base_version", String(baseVersion));

  const { data } = await apiClient.post<FileNode>(`/files/${fileId}/upload`, form, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (event: AxiosProgressEvent) => {
      if (onProgress && event.total) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    },
  });
  return data;
}

/** 버전 히스토리 조회 (내림차순) — PRD 3.3. */
export async function listVersions(fileId: number): Promise<FileVersionListResponse> {
  const { data } = await apiClient.get<FileVersionListResponse>(`/files/${fileId}/versions`);
  return data;
}

/** 과거 버전을 새 버전으로 복구 (이력 보존) — PRD 3.3. */
export async function restoreVersion(fileId: number, version: number): Promise<FileNode> {
  const { data } = await apiClient.post<FileNode>(`/files/${fileId}/versions/${version}/restore`);
  return data;
}

/** 현재 버전 다운로드 티켓 발급 (무헤더 스트리밍 다운로드용). */
export async function issueDownloadTicket(fileId: number): Promise<DownloadTicketResponse> {
  const { data } = await apiClient.post<DownloadTicketResponse>(`/files/${fileId}/download-ticket`);
  return data;
}

/**
 * 폴더/다중 선택 ZIP 다운로드 티켓 발급.
 * 폴더는 하위 전체가 상대 경로 그대로 담긴다. 개수·용량 상한을 넘으면 413.
 */
export async function issueArchiveDownloadTicket(
  fileIds: number[],
): Promise<DownloadTicketResponse> {
  const { data } = await apiClient.post<DownloadTicketResponse>(
    "/files/download-archive-ticket",
    { file_ids: fileIds },
  );
  return data;
}

/** 특정 버전 다운로드 티켓 발급. */
export async function issueVersionDownloadTicket(
  fileId: number,
  version: number,
): Promise<DownloadTicketResponse> {
  const { data } = await apiClient.post<DownloadTicketResponse>(
    `/files/${fileId}/versions/${version}/download-ticket`,
  );
  return data;
}

export async function createFolder(name: string, parentId: number | null): Promise<FileNode> {
  const { data } = await apiClient.post<FileNode>("/files", { name, parent_id: parentId });
  return data;
}

export async function renameFile(id: number, name: string): Promise<FileNode> {
  const { data } = await apiClient.put<FileNode>(`/files/${id}`, { name });
  return data;
}

/** 다른 폴더로 이동. parentId 가 null 이면 내 드라이브 루트. */
export async function moveFile(id: number, parentId: number | null): Promise<FileNode> {
  const { data } = await apiClient.post<FileNode>(`/files/${id}/move`, {
    parent_id: parentId,
  });
  return data;
}

export async function softDeleteFile(id: number): Promise<void> {
  await apiClient.post(`/files/${id}/delete`);
}

// --- 즐겨찾기 (Phase 8-2) --------------------------------------------------

/** 즐겨찾기 등록 (멱등). read 이상 접근 가능한 파일만 허용된다. */
export async function addFavorite(id: number): Promise<void> {
  await apiClient.put(`/files/${id}/favorite`);
}

/** 즐겨찾기 해제 (멱등). */
export async function removeFavorite(id: number): Promise<void> {
  await apiClient.delete(`/files/${id}/favorite`);
}

/** 내 즐겨찾기 목록 (페이지네이션). 삭제·접근불가 항목은 서버가 숨긴다. */
export async function listFavorites(page = 1, size = 50): Promise<FileListResponse> {
  const { data } = await apiClient.get<FileListResponse>("/files/favorites", {
    params: { page, size },
  });
  return data;
}

// --- 최근 이용 항목 (Phase 8-3) --------------------------------------------

/** 최근 이용 항목(최신순, 최대 50). 미리보기·다운로드 시 기록된다. */
export async function listRecent(limit = 20): Promise<FileNode[]> {
  const { data } = await apiClient.get<FileNode[]>("/files/recent", {
    params: { limit },
  });
  return data;
}

export async function listTrash(): Promise<FileNode[]> {
  const { data } = await apiClient.get<FileNode[]>("/files/trash");
  return data;
}

export async function restoreFile(id: number): Promise<FileNode> {
  const { data } = await apiClient.post<FileNode>(`/files/${id}/restore-trash`);
  return data;
}

export async function permanentDeleteFile(id: number): Promise<void> {
  await apiClient.post(`/files/${id}/permanent-delete`);
}

// --- 썸네일/미리보기 (PRD 3.2) --------------------------------------------

/**
 * 썸네일 이미지를 blob 으로 가져온다. 인증 헤더가 필요하므로 <img src> 직접 지정이 불가해
 * fetch 후 objectURL 로 렌더한다. 이미지가 아니거나 아직 생성되지 않았으면 404 → 호출부가
 * 파일 유형 기본 아이콘으로 폴백한다.
 */
export async function fetchThumbnail(fileId: number): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(`/files/${fileId}/thumbnail`, {
    responseType: "blob",
  });
  return data;
}

// --- 재개 가능 업로드 (PRD 3.2) --------------------------------------------

/** 새 파일 재개 업로드 세션 개시. 응답의 part_size 로 청크를 나눈다. */
export async function initResumableUpload(payload: {
  filename: string;
  parent_id?: number | null;
  total_size: number;
  mime_type?: string | null;
}): Promise<ResumableSession> {
  const { data } = await apiClient.post<ResumableSession>("/files/uploads", payload);
  return data;
}

/** 기존 파일 재업로드(새 버전) 재개 세션 개시. base_version 으로 충돌 감지. */
export async function initResumableReupload(
  fileId: number,
  payload: { total_size: number; mime_type?: string | null; base_version?: number | null },
): Promise<ResumableSession> {
  const { data } = await apiClient.post<ResumableSession>(`/files/${fileId}/uploads`, payload);
  return data;
}

/** 세션 상태/재개 정보. uploaded_parts 로 빠진 파트만 재전송한다. */
export async function getResumableSession(sessionId: string): Promise<ResumableSession> {
  const { data } = await apiClient.get<ResumableSession>(`/files/uploads/${sessionId}`);
  return data;
}

/** 단일 파트를 raw 바이트로 전송(멀티파트 폼 아님). 같은 파트 재전송 시 덮어쓴다. */
export async function uploadResumablePart(
  sessionId: string,
  partNumber: number,
  chunk: Blob,
  opts?: { onProgress?: (loaded: number) => void; signal?: AbortSignal },
): Promise<ResumablePart> {
  const { data } = await apiClient.put<ResumablePart>(
    `/files/uploads/${sessionId}/parts/${partNumber}`,
    chunk,
    {
      headers: { "Content-Type": "application/octet-stream" },
      signal: opts?.signal,
      onUploadProgress: (event) => opts?.onProgress?.(event.loaded),
    },
  );
  return data;
}

/** 업로드 완료 = 파트 병합 → 파일 확정 (201 FileResponse). */
export async function completeResumableUpload(sessionId: string): Promise<FileNode> {
  const { data } = await apiClient.post<FileNode>(`/files/uploads/${sessionId}/complete`);
  return data;
}

/** 업로드 세션 중단 — 스테이징 파트 폐기. */
export async function abortResumableUpload(sessionId: string): Promise<void> {
  await apiClient.delete(`/files/uploads/${sessionId}`);
}
