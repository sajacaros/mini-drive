/** 파일/폴더 API (PRD 6.2). */

import type { AxiosProgressEvent } from "axios";

import apiClient from "./client";
import type { FileListResponse, FileNode } from "./types";

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
