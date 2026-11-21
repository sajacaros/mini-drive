/**
 * BeeMate PC 수신 화면 (spec/beemate.md 「PC 수신 화면」).
 *
 * 이 탭이 열려 있는 동안 이 브라우저가 기기 하나로 동작한다. 폰에서 보낸 것은
 *  - 이미지 → 클립보드(포커스가 있을 때. 없으면 알림 → 탭으로 돌아오는 순간 복사) + 폴더에 사본
 *  - 그 외 → 고른 폴더(없거나 미지원이면 브라우저 다운로드 폴더)
 * 로 처리하고, 처리가 끝나면 서버에 받음 확인(=삭제)을 보낸다.
 *
 * 순서: 스트림을 먼저 열고 `: connected` 를 받은 뒤 밀린 목록을 조회한다(lib/sse.ts). 둘 다에
 * 같은 전송이 올 수 있어 id 로 거른다. 처리는 한 줄로 세운다 — 동시에 여러 장을 폴더에 쓰면
 * 같은 이름 비켜 가기가 서로를 못 본다.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ackTransfer,
  type BeemateDevice,
  type BeemateTransfer,
  createDevice,
  deleteDevice,
  fetchContent,
  listDevices,
  listPending,
  renameDevice,
} from "@/api/beemate";
import { extractErrorMessage } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { ErrorState, LoadingState } from "@/components/ui";
import { InboxIcon } from "@/components/icons";
import {
  copyImageToClipboard,
  type DirHandle,
  type FolderState,
  forgetFolder,
  isImage,
  loadDeviceId,
  loadFolder,
  pickFolder,
  regrantFolder,
  saveAsDownload,
  saveDeviceId,
  saveToFolder,
} from "@/lib/beemateReceive";
import { formatBytes, formatDateTime } from "@/lib/format";
import { subscribeSse } from "@/lib/sse";
import { useAuthStore } from "@/store/auth";

type LogStatus = "receiving" | "waiting-focus" | "done" | "error";

interface LogItem {
  id: number;
  name: string;
  mime: string;
  size: number;
  status: LogStatus;
  note: string;
  at: string;
}

const PLATFORM_LABEL: Record<string, string> = { android: "안드로이드", ios: "iPhone", web: "PC" };
const DEVICES_REFRESH_MS = 30_000;
const LOG_LIMIT = 50;

function notify(title: string, body: string): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, { body, tag: "beemate" });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch {
    /* 일부 환경(서비스 워커 필수)에서는 생성자가 막혀 있다 — 알림은 부가 기능 */
  }
}

export function BeematePage() {
  const toast = useToast();
  const user = useAuthStore((s) => s.user);
  const userId = user?.id ?? 0;

  const [deviceId, setDeviceId] = useState<number | null>(() => loadDeviceId(userId));
  const [devices, setDevices] = useState<BeemateDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("내 PC");
  const [registering, setRegistering] = useState(false);

  const [folder, setFolder] = useState<FolderState>({ kind: "none" });
  const folderRef = useRef<DirHandle | null>(null);
  const [notifPerm, setNotifPerm] = useState<NotificationPermission | "unsupported">(() =>
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );

  const [log, setLog] = useState<LogItem[]>([]);
  const seen = useRef(new Set<number>());
  const queue = useRef<Promise<void>>(Promise.resolve());
  // 포커스를 기다리는 이미지 — 클립보드는 한 장만 담으므로 마지막 것만 의미가 있다.
  const waitingClip = useRef<{ id: number; blob: Blob } | null>(null);

  const patchLog = useCallback((id: number, patch: Partial<LogItem>) => {
    setLog((items) => items.map((it) => (it.id === id ? { ...it, ...patch } : it)));
  }, []);

  // --- 기기 목록 ----------------------------------------------------------------

  const refreshDevices = useCallback(async () => {
    try {
      const list = await listDevices();
      setDevices(list);
      setError(null);
      return list;
    } catch (err) {
      setError(extractErrorMessage(err, "기기 목록을 불러오지 못했습니다."));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshDevices().then((list) => {
      // 다른 곳에서 이 기기를 지웠다면 등록 화면으로 돌아간다.
      if (list && deviceId != null && !list.some((d) => d.id === deviceId)) {
        saveDeviceId(userId, null);
        setDeviceId(null);
      }
    });
    const t = setInterval(() => void refreshDevices(), DEVICES_REFRESH_MS);
    return () => clearInterval(t);
    // deviceId 변화로 다시 돌 필요는 없다 — 등록·삭제 경로가 직접 갱신한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshDevices, userId]);

  useEffect(() => {
    void loadFolder().then(({ handle, state }) => {
      folderRef.current = handle;
      setFolder(state);
    });
  }, []);

  // --- 받기 -----------------------------------------------------------------------

  const finishClipboard = useCallback(async () => {
    const item = waitingClip.current;
    if (!item || !document.hasFocus()) return;
    waitingClip.current = null;
    try {
      await copyImageToClipboard(item.blob);
      await ackTransfer(item.id).catch(() => undefined);
      patchLog(item.id, { status: "done", note: "클립보드에 복사됨" });
      document.title = "BeeMate";
    } catch (err) {
      patchLog(item.id, { status: "error", note: extractErrorMessage(err, "클립보드 복사 실패") });
    }
  }, [patchLog]);

  const receive = useCallback(
    async (t: BeemateTransfer) => {
      const item: LogItem = {
        id: t.id,
        name: t.name,
        mime: t.mime_type,
        size: t.size,
        status: "receiving",
        note: "받는 중",
        at: new Date().toISOString(),
      };
      setLog((items) => [item, ...items].slice(0, LOG_LIMIT));
      try {
        const blob = await fetchContent(t.id);
        const image = isImage(t.mime_type);
        const dir = folderRef.current;
        let savedAs: string | null = null;
        if (dir && (await dir.queryPermission({ mode: "readwrite" })) === "granted") {
          savedAs = await saveToFolder(dir, t.name, blob);
        } else if (!image) {
          saveAsDownload(t.name, blob);
          savedAs = "다운로드 폴더";
        }
        const where = savedAs ? ` · ${savedAs === "다운로드 폴더" ? savedAs : `${savedAs} 저장`}` : "";

        if (image && document.hasFocus()) {
          await copyImageToClipboard(blob);
          await ackTransfer(t.id);
          patchLog(t.id, { status: "done", note: `클립보드에 복사됨${where}` });
        } else if (image) {
          // 포커스가 없으면 브라우저가 클립보드 쓰기를 거절한다. 폴더에 사본이 있으면 서버에서는
          // 지워도 되지만, 없으면 복사가 끝날 때까지 서버에 남겨 둔다(탭을 닫아도 다시 받는다).
          if (savedAs) await ackTransfer(t.id);
          const prev = waitingClip.current;
          if (prev) {
            // 클립보드는 한 장만 담는다 — 앞 사진은 복사 기회가 없으므로 서버에서도 정리한다.
            await ackTransfer(prev.id).catch(() => undefined);
            patchLog(prev.id, { status: "done", note: "다음 사진이 도착해 복사를 건너뜀" });
          }
          waitingClip.current = { id: t.id, blob };
          patchLog(t.id, { status: "waiting-focus", note: `탭으로 돌아오면 복사합니다${where}` });
          document.title = "(사진 도착) BeeMate";
          notify("사진이 도착했습니다", `${t.name} — 눌러서 클립보드에 복사`);
        } else {
          await ackTransfer(t.id);
          patchLog(t.id, { status: "done", note: savedAs ? `저장됨 · ${savedAs}` : "저장됨" });
          if (!document.hasFocus()) notify("파일이 도착했습니다", t.name);
        }
      } catch (err) {
        seen.current.delete(t.id); // 다음 접속 때 다시 시도한다
        patchLog(t.id, { status: "error", note: extractErrorMessage(err, "받지 못했습니다") });
      }
    },
    [patchLog],
  );

  const enqueue = useCallback(
    (t: BeemateTransfer) => {
      if (seen.current.has(t.id)) return;
      seen.current.add(t.id);
      queue.current = queue.current.then(() => receive(t));
    },
    [receive],
  );

  useEffect(() => {
    if (deviceId == null) return;
    const unsubscribe = subscribeSse<BeemateTransfer>(`/api/beemate/devices/${deviceId}/events`, {
      onEvent: enqueue,
      onOpen: () => {
        // 접속 직후 온라인 표시를 바로 반영한다(주기 갱신은 30초 간격이라 그동안 회색으로 남는다).
        void refreshDevices();
        void listPending(deviceId)
          .then((items) => items.forEach(enqueue))
          .catch(() => undefined);
      },
      onFatal: () => {
        saveDeviceId(userId, null);
        setDeviceId(null);
        toast.error("이 기기가 삭제되었습니다. 다시 등록해 주세요.");
      },
    });
    return unsubscribe;
  }, [deviceId, enqueue, refreshDevices, toast, userId]);

  useEffect(() => {
    const onFocus = () => void finishClipboard();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [finishClipboard]);

  // --- 동작 -----------------------------------------------------------------------

  const onRegister = async () => {
    const name = newName.trim();
    if (!name) return;
    setRegistering(true);
    try {
      const d = await createDevice(name, "web");
      saveDeviceId(userId, d.id);
      setDeviceId(d.id);
      await refreshDevices();
      toast.success(`"${d.name}" 으로 등록했습니다.`);
    } catch (err) {
      toast.error(extractErrorMessage(err, "기기를 등록하지 못했습니다."));
    } finally {
      setRegistering(false);
    }
  };

  const onPickFolder = async () => {
    try {
      const handle = await pickFolder();
      folderRef.current = handle;
      setFolder({ kind: "ready", name: handle.name });
    } catch (err) {
      if ((err as DOMException)?.name !== "AbortError") {
        toast.error(extractErrorMessage(err, "폴더를 고르지 못했습니다."));
      }
    }
  };

  const onRegrant = async () => {
    const handle = folderRef.current;
    if (!handle) return;
    if (await regrantFolder(handle)) setFolder({ kind: "ready", name: handle.name });
  };

  const onForgetFolder = async () => {
    await forgetFolder();
    folderRef.current = null;
    setFolder({ kind: "none" });
  };

  const onAskNotification = async () => {
    if (typeof Notification === "undefined") return;
    setNotifPerm(await Notification.requestPermission());
  };

  const onRename = async (d: BeemateDevice) => {
    const name = window.prompt("기기 이름", d.name)?.trim();
    if (!name || name === d.name) return;
    try {
      await renameDevice(d.id, name);
      await refreshDevices();
    } catch (err) {
      toast.error(extractErrorMessage(err, "이름을 바꾸지 못했습니다."));
    }
  };

  const onDelete = async (d: BeemateDevice) => {
    const self = d.id === deviceId;
    const msg = self
      ? "이 PC 를 기기 목록에서 지울까요? 아직 받지 않은 파일도 함께 지워집니다."
      : `"${d.name}" 을(를) 지울까요? 그 기기로 가던 받지 않은 파일도 함께 지워집니다.`;
    if (!window.confirm(msg)) return;
    try {
      await deleteDevice(d.id);
      if (self) {
        saveDeviceId(userId, null);
        setDeviceId(null);
      }
      await refreshDevices();
    } catch (err) {
      toast.error(extractErrorMessage(err, "기기를 지우지 못했습니다."));
    }
  };

  // --- 화면 -----------------------------------------------------------------------

  const me = devices.find((d) => d.id === deviceId) ?? null;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-token px-4 py-3 md:px-6 md:py-4">
        <PageHeader align="start">
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <span className="text-accent">
              <InboxIcon width={18} height={18} />
            </span>
            BeeMate
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            폰에서 보낸 사진은 클립보드로, 파일은 폴더로 받습니다. 이 탭을 열어 두세요.
          </p>
        </PageHeader>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 md:px-6">
        {loading ? (
          <LoadingState />
        ) : error && devices.length === 0 ? (
          <ErrorState message={error} onRetry={() => void refreshDevices()} />
        ) : deviceId == null ? (
          <section className="card mx-auto max-w-md space-y-3 p-5">
            <h2 className="font-semibold">이 PC 를 기기로 등록</h2>
            <p className="text-sm text-muted">
              폰의 BeeMate 앱에서 보낼 곳을 고를 때 이 이름이 보입니다.
            </p>
            <label className="label" htmlFor="beemate-name">
              기기 이름
            </label>
            <input
              id="beemate-name"
              className="input w-full"
              value={newName}
              maxLength={100}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void onRegister()}
            />
            <button
              className="btn btn-primary w-full"
              disabled={registering || !newName.trim()}
              onClick={() => void onRegister()}
            >
              등록
            </button>
          </section>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            <section className="card grid gap-4 p-4 md:grid-cols-3">
              <div>
                <div className="label">이 기기</div>
                <div className="mt-1 flex items-center gap-2 font-medium">
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${me?.online ? "bg-[color:var(--success)]" : "bg-muted-token"}`}
                  />
                  {me?.name ?? "…"}
                </div>
              </div>

              <div>
                <div className="label">저장 폴더</div>
                <div className="mt-1 text-sm">
                  {folder.kind === "unsupported" && (
                    <span className="text-muted">
                      이 브라우저는 폴더 지정을 지원하지 않아 다운로드 폴더에 저장합니다 (Chrome·Edge
                      권장).
                    </span>
                  )}
                  {folder.kind === "none" && (
                    <button className="btn btn-secondary" onClick={() => void onPickFolder()}>
                      폴더 고르기
                    </button>
                  )}
                  {folder.kind === "needs-permission" && (
                    <button className="btn btn-primary" onClick={() => void onRegrant()}>
                      “{folder.name}” 다시 허용
                    </button>
                  )}
                  {folder.kind === "ready" && (
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{folder.name}</span>
                      <button className="btn btn-ghost" onClick={() => void onPickFolder()}>
                        바꾸기
                      </button>
                      <button className="btn btn-ghost" onClick={() => void onForgetFolder()}>
                        해제
                      </button>
                    </span>
                  )}
                </div>
              </div>

              <div>
                <div className="label">알림</div>
                <div className="mt-1 text-sm">
                  {notifPerm === "granted" && <span>켜짐</span>}
                  {notifPerm === "default" && (
                    <button className="btn btn-secondary" onClick={() => void onAskNotification()}>
                      알림 허용
                    </button>
                  )}
                  {notifPerm === "denied" && (
                    <span className="text-muted">차단됨 — 브라우저 사이트 설정에서 허용하세요</span>
                  )}
                  {notifPerm === "unsupported" && <span className="text-muted">지원 안 함</span>}
                </div>
              </div>
            </section>

            <section className="card p-0">
              <h2 className="border-b border-token px-4 py-2 text-sm font-semibold">받은 항목</h2>
              {log.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-muted">
                  아직 받은 것이 없습니다. 폰에서 보내면 여기에 나타납니다.
                </p>
              ) : (
                <ul className="divide-token">
                  {log.map((it) => (
                    <li key={it.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{it.name}</div>
                        <div
                          className={`text-xs ${it.status === "error" ? "text-danger" : it.status === "waiting-focus" ? "text-warning" : "text-muted"}`}
                        >
                          {formatBytes(it.size)} · {it.note}
                        </div>
                      </div>
                      {it.status === "waiting-focus" && (
                        <button className="btn btn-primary" onClick={() => void finishClipboard()}>
                          지금 복사
                        </button>
                      )}
                      <span className="shrink-0 text-xs text-muted">{formatDateTime(it.at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card p-0">
              <h2 className="border-b border-token px-4 py-2 text-sm font-semibold">내 기기</h2>
              <ul className="divide-token">
                {devices.map((d) => (
                  <li key={d.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                    <span
                      title={d.online ? "접속 중" : "오프라인"}
                      className={`inline-block h-2 w-2 shrink-0 rounded-full ${d.online ? "bg-[color:var(--success)]" : "bg-muted-token"}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">
                        {d.name}
                        {d.id === deviceId && <span className="ml-2 text-xs text-accent">이 PC</span>}
                      </div>
                      <div className="text-xs text-muted">
                        {PLATFORM_LABEL[d.platform] ?? d.platform}
                        {d.last_seen_at && ` · 마지막 접속 ${formatDateTime(d.last_seen_at)}`}
                      </div>
                    </div>
                    <button className="btn btn-ghost" onClick={() => void onRename(d)}>
                      이름
                    </button>
                    <button className="btn btn-ghost text-danger" onClick={() => void onDelete(d)}>
                      삭제
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
