/** 파일/폴더 API (PRD 6.2). */

import type { AxiosProgressEvent } from "axios";

import apiClient from "./client";
import type {
  DownloadTicketResponse,
  FileListResponse,
  FileNode,
  FileVersionListResponse,
} from "./types";

export async function listFiles(
  parentId: number | null,
  page = 1,
  size = 50,
): Promise<FileListResponse> {
  const { data } = await apiClient.get<FileListResponse>("/files", {
    params: { parentId: parentId ?? undefined, page, size },
  });
  return data;
}

export async function getFile(id: number): Promise<FileNode> {
  const { data } = await apiClient.get<FileNode>(`/files/${id}`);
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

export async function softDeleteFile(id: number): Promise<void> {
  await apiClient.post(`/files/${id}/delete`);
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
