/** BeeMate — 내 기기끼리 파일 보내기 API (spec/beemate.md). */

import apiClient from "./client";

export type BeematePlatform = "android" | "ios" | "web";

export interface BeemateDevice {
  id: number;
  name: string;
  platform: BeematePlatform;
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

export async function listDevices(): Promise<BeemateDevice[]> {
  const { data } = await apiClient.get<BeemateDevice[]>("/beemate/devices");
  return data;
}

export async function createDevice(
  name: string,
  platform: BeematePlatform,
): Promise<BeemateDevice> {
  const { data } = await apiClient.post<BeemateDevice>("/beemate/devices", { name, platform });
  return data;
}

export async function renameDevice(id: number, name: string): Promise<BeemateDevice> {
  const { data } = await apiClient.patch<BeemateDevice>(`/beemate/devices/${id}`, { name });
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
