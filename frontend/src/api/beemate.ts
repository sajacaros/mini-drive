/** BeeMate — 내 기기끼리 파일 보내기 API (spec/beemate.md). */

import apiClient from "./client";

export type BeematePlatform = "android" | "ios" | "web";

export interface BeemateDevice {
  id: number;
  name: string;
  platform: BeematePlatform;
  /** 즐겨찾기 — 꺼져 있어도 목록 맨 위. */
  is_favorite: boolean;
  /** 숨김 — 보낼 곳 목록에서 빠진다(관리 목록에는 남는다). */
  is_hidden: boolean;
  created_at: string;
  last_seen_at: string | null;
  /** 이벤트 스트림이 지금 붙어 있는가. */
  online: boolean;
}

export interface BeemateTransfer {
  id: number;
  to_device_id: number;
  from_device_id: number | null;
  name: string;
  mime_type: string;
  size: number;
  created_at: string;
  expires_at: string;
}

/** 즐겨찾기 → 켜진 기기 → 나머지 순(서버 정렬). 기본은 보낼 곳 목록이라 숨긴 기기를 뺀다. */
export async function listDevices(includeHidden = false): Promise<BeemateDevice[]> {
  const { data } = await apiClient.get<BeemateDevice[]>("/beemate/devices", {
    params: includeHidden ? { include_hidden: true } : undefined,
  });
  return data;
}

export interface BeemateDevicePatch {
  name?: string;
  is_favorite?: boolean;
  is_hidden?: boolean;
}

export async function updateDevice(id: number, patch: BeemateDevicePatch): Promise<BeemateDevice> {
  const { data } = await apiClient.patch<BeemateDevice>(`/beemate/devices/${id}`, patch);
  return data;
}

export async function deleteDevice(id: number): Promise<void> {
  await apiClient.delete(`/beemate/devices/${id}`);
}

export async function listPending(deviceId: number): Promise<BeemateTransfer[]> {
  const { data } = await apiClient.get<BeemateTransfer[]>("/beemate/transfers", {
    params: { device_id: deviceId },
  });
  return data;
}

/** 내용 받기. 게이트웨이가 스트리밍하지만 클립보드·폴더 저장에 Blob 이 필요하다(상한 500 MiB). */
export async function fetchContent(id: number): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(`/beemate/transfers/${id}/content`, {
    responseType: "blob",
  });
  return data;
}

/** SSE 프레임 — 내용은 싣지 않는다. hello 로 내 기기 id 를 알고, pending 이면 목록을 당긴다. */
export type BeemateSignal = { type: "hello"; device_id: number } | { type: "pending" };

/** 받음 확인 = 서버에서 삭제. */
export async function ackTransfer(id: number): Promise<void> {
  await apiClient.delete(`/beemate/transfers/${id}`);
}

export async function sendFile(
  file: File,
  toDeviceId: number,
  fromDeviceId?: number | null,
): Promise<BeemateTransfer> {
  const form = new FormData();
  form.append("file", file);
  form.append("to_device_id", String(toDeviceId));
  if (fromDeviceId != null) form.append("from_device_id", String(fromDeviceId));
  const { data } = await apiClient.post<BeemateTransfer>("/beemate/transfers", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}
