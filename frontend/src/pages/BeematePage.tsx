/**
 * BeeMate PC 수신 화면 + 기기 관리 (spec/beemate.md 「PC 수신 화면」).
 *
 * 설정 단계가 없다. 탭을 열면 이 브라우저가 기기로 자동 등록되고(client_id), 바로 받는다.
 *  - 이미지 → 클립보드(포커스가 있을 때. 없으면 알림 → 탭으로 돌아오는 순간 복사) + 폴더에 사본
 *  - 그 외 → 고른 폴더, 안 골랐으면 브라우저 다운로드 폴더(기본값)
 * 처리가 끝나면 서버에 받음 확인(=삭제)을 보낸다.
 *
 * 스트림에는 내용이 오지 않는다. `hello`(내 기기 id)를 받으면 한 번, `pending` 신호마다 한 번
 * 목록을 당겨 간다(pull). 꺼져 있던 동안 온 것도 같은 경로로 받는다. 같은 전송이 두 번 당겨질
 * 수 있어 id 로 거르고, 처리는 한 줄로 세운다(폴더의 같은 이름 비켜 가기가 서로를 보도록).
 *
 * 같은 브라우저에서 탭을 여러 개 열면 같은 기기라 사진이 두 번 복사된다 — Web Locks 로 한
 * 탭만 받고, 그 탭을 닫으면 남은 탭이 이어받는다.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ackTransfer,
  type BeemateDevice,
  type BeemateSignal,
  type BeemateTransfer,
  deleteDevice,
  fetchContent,
  listDevices,
  listPending,
  updateDevice,
} from "@/api/beemate";
import { extractErrorMessage } from "@/api/client";
import { FavoriteStar } from "@/components/FavoriteStar";
import { PageHeader } from "@/components/PageHeader";
import { useToast } from "@/components/Toast";
import { InboxIcon } from "@/components/icons";
import {
  copyImageToClipboard,
  defaultDeviceName,
  type DirHandle,
  type FolderState,
  forgetFolder,
  getClientId,
  isImage,
  loadFolder,
  pickFolder,
  regrantFolder,
  saveAsDownload,
  saveToFolder,
} from "@/lib/beemateReceive";
import { formatBytes, formatDateTime } from "@/lib/format";
import { subscribeSse } from "@/lib/sse";
import { useAuthStore } from "@/store/auth";

type LogStatus = "receiving" | "waiting-focus" | "done" | "error";

interface LogItem {
  id: number;
  name: string;
  size: number;
  status: LogStatus;
  note: string;
  at: string;
}

/** 이 탭의 수신 상태. other-tab = 같은 브라우저의 다른 탭이 받는 중. */
type ReceiverState = "connecting" | "active" | "other-tab" | { error: string };

const PLATFORM_LABEL: Record<string, string> = { android: "안드로이드", ios: "iPhone", web: "PC" };
const DEVICES_REFRESH_MS = 30_000;
const LOG_LIMIT = 50;
const DOWNLOADS = "다운로드 폴더";

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

/** 같은 브라우저의 탭 중 하나만 run 을 돌린다. 반환 함수로 내려놓는다(대기 중이면 대기 취소). */
function holdReceiverLock(
  name: string,
  run: () => () => void,
  onWaiting: () => void,
): () => void {
  const locks = (navigator as Navigator & { locks?: LockManager }).locks;
  if (!locks) return run(); // 미지원 브라우저 — 탭마다 받는다(중복 가능, 드묾)
  const abort = new AbortController();
  let release: (() => void) | null = null;
  let cleanup: (() => void) | null = null;
  void locks
    .request(name, { ifAvailable: true }, async (lock) => {
      if (lock) return hold();
      onWaiting();
      // 다른 탭이 쥐고 있다 — 그 탭이 닫힐 때까지 줄을 선다.
      return locks.request(name, { signal: abort.signal }, hold);
    })
    .catch(() => undefined); // abort 로 대기 취소
  function hold(): Promise<void> {
    cleanup = run();
    return new Promise<void>((resolve) => {
      release = resolve;
    });
  }
  return () => {
    abort.abort();
    cleanup?.();
    release?.();
  };
}

export function BeematePage() {
  const toast = useToast();
  const userId = useAuthStore((s) => s.user?.id ?? 0);

  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [receiver, setReceiver] = useState<ReceiverState>("connecting");
  const [devices, setDevices] = useState<BeemateDevice[]>([]);
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);

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
      setDevices(await listDevices(true));
    } catch {
      /* 다음 주기에 다시 — 받기는 목록과 무관하게 계속된다 */
    }
  }, []);

  useEffect(() => {
    void refreshDevices();
    const t = setInterval(() => void refreshDevices(), DEVICES_REFRESH_MS);
    return () => clearInterval(t);
  }, [refreshDevices]);

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
      setLog((items) =>
        [
          {
            id: t.id,
            name: t.name,
            size: t.size,
            status: "receiving" as const,
            note: "받는 중",
            at: new Date().toISOString(),
          },
          ...items,
        ].slice(0, LOG_LIMIT),
      );
      try {
        const blob = await fetchContent(t.id);
        const image = isImage(t.mime_type);
        const dir = folderRef.current;
        let savedAs: string | null = null;
        if (dir && (await dir.queryPermission({ mode: "readwrite" })) === "granted") {
          savedAs = await saveToFolder(dir, t.name, blob);
        } else if (!image) {
          saveAsDownload(t.name, blob);
          savedAs = DOWNLOADS;
        }
        const where = savedAs ? ` · ${savedAs === DOWNLOADS ? savedAs : `${savedAs} 저장`}` : "";

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
          patchLog(t.id, { status: "done", note: `저장됨 · ${savedAs ?? DOWNLOADS}` });
          if (!document.hasFocus()) notify("파일이 도착했습니다", t.name);
        }
      } catch (err) {
        seen.current.delete(t.id); // 다음 신호·재접속 때 다시 시도한다
        patchLog(t.id, { status: "error", note: extractErrorMessage(err, "받지 못했습니다") });
      }
    },
    [patchLog],
  );

  const pull = useCallback(
    async (id: number) => {
      let items: BeemateTransfer[];
      try {
        items = await listPending(id);
      } catch {
        return; // 다음 신호나 재접속 때 다시 당긴다
      }
      for (const t of items) {
        if (seen.current.has(t.id)) continue;
        seen.current.add(t.id);
        queue.current = queue.current.then(() => receive(t));
      }
    },
    [receive],
  );

  useEffect(() => {
    if (!userId) return;
    const clientId = getClientId();
    const qs = new URLSearchParams({
      client_id: clientId,
      name: defaultDeviceName(),
      platform: "web",
    });
    let myId: number | null = null;
    return holdReceiverLock(
      `beemate-receiver-${userId}`,
      () => {
        setReceiver("connecting");
        return subscribeSse<BeemateSignal>(`/api/beemate/events?${qs}`, {
          onEvent: (signal) => {
            if (signal.type === "hello") {
              myId = signal.device_id;
              setDeviceId(signal.device_id);
              setReceiver("active");
              void refreshDevices(); // 접속 직후 온라인 표시를 바로 반영
              void pull(signal.device_id);
            } else if (signal.type === "pending" && myId != null) {
              void pull(myId);
            }
          },
          onFatal: (_status, detail) => {
            setReceiver({ error: detail || "이 기기로 받을 수 없습니다." });
          },
        });
      },
      () => setReceiver("other-tab"),
    );
  }, [userId, pull, refreshDevices]);

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

  const patchDevice = async (d: BeemateDevice, patch: Parameters<typeof updateDevice>[1]) => {
    // 낙관적 반영 — 실패하면 서버 목록으로 되돌린다.
    setDevices((list) => list.map((x) => (x.id === d.id ? { ...x, ...patch } : x)));
    try {
      await updateDevice(d.id, patch);
    } catch (err) {
      toast.error(extractErrorMessage(err, "기기 설정을 바꾸지 못했습니다."));
    }
    await refreshDevices();
  };

  const submitRename = async () => {
    if (!editing) return;
    const d = devices.find((x) => x.id === editing.id);
    const name = editing.name.trim();
    setEditing(null);
    if (d && name && name !== d.name) await patchDevice(d, { name });
  };

  const onDelete = async (d: BeemateDevice) => {
    const self = d.id === deviceId;
    const msg = self
      ? "이 브라우저를 기기 목록에서 지울까요? 이 탭을 다시 열면 새 기기로 등록됩니다."
      : `"${d.name}" 을(를) 지울까요? 그 기기로 가던 받지 않은 파일도 함께 지워집니다.`;
    if (!window.confirm(msg)) return;
    try {
      await deleteDevice(d.id);
      await refreshDevices();
    } catch (err) {
      toast.error(extractErrorMessage(err, "기기를 지우지 못했습니다."));
    }
  };

  // --- 화면 -----------------------------------------------------------------------

  const me = devices.find((d) => d.id === deviceId) ?? null;
  const statusText =
    receiver === "active"
      ? "받을 준비 완료"
      : receiver === "connecting"
        ? "연결 중…"
        : receiver === "other-tab"
          ? "이 브라우저의 다른 탭에서 받고 있습니다"
          : receiver.error;

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
        <div className="mx-auto max-w-3xl space-y-4">
          <section className="card grid gap-4 p-4 md:grid-cols-3">
            <div>
              <div className="label">이 브라우저</div>
              <div className="mt-1 flex items-center gap-2 font-medium">
                <span
                  className={`inline-block h-2 w-2 shrink-0 rounded-full ${receiver === "active" ? "bg-[color:var(--success)]" : "bg-muted-token"}`}
                />
                <span className="truncate">{me?.name ?? defaultDeviceName()}</span>
              </div>
              <div
                className={`mt-0.5 text-xs ${typeof receiver === "object" ? "text-danger" : "text-muted"}`}
              >
                {statusText}
              </div>
            </div>

            <div>
              <div className="label">저장 위치</div>
              <div className="mt-1 text-sm">
                {folder.kind === "ready" ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{folder.name}</span>
                    <button className="btn btn-ghost" onClick={() => void onPickFolder()}>
                      바꾸기
                    </button>
                    <button className="btn btn-ghost" onClick={() => void onForgetFolder()}>
                      해제
                    </button>
                  </span>
                ) : (
                  <>
                    <div className="font-medium">{DOWNLOADS}</div>
                    {folder.kind === "none" && (
                      <button
                        className="btn btn-ghost mt-1 px-0"
                        onClick={() => void onPickFolder()}
                      >
                        전용 폴더 지정
                      </button>
                    )}
                    {folder.kind === "needs-permission" && (
                      <button className="btn btn-secondary mt-1" onClick={() => void onRegrant()}>
                        “{folder.name}” 다시 허용
                      </button>
                    )}
                  </>
                )}
              </div>
            </div>

            <div>
              <div className="label">알림</div>
              <div className="mt-1 text-sm">
                {notifPerm === "granted" && <span>켜짐</span>}
                {notifPerm === "default" && (
                  <button className="btn btn-secondary" onClick={() => void onAskNotification()}>
                    알림 켜기
                  </button>
                )}
                {notifPerm === "denied" && (
                  <span className="text-muted">차단됨 — 브라우저 사이트 설정에서 허용하세요</span>
                )}
                {notifPerm === "unsupported" && <span className="text-muted">지원 안 함</span>}
              </div>
              {notifPerm === "default" && (
                <div className="mt-0.5 text-xs text-muted">탭이 뒤에 있을 때 사진 도착을 알려요</div>
              )}
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
            <div className="border-b border-token px-4 py-2">
              <h2 className="text-sm font-semibold">기기 관리</h2>
              <p className="text-xs text-muted">
                ★ 는 보낼 곳 목록 맨 위에 고정됩니다. 끈 기기는 보낼 곳 목록에 나오지 않습니다.
              </p>
            </div>
            <ul className="divide-token">
              {devices.map((d) => (
                <li key={d.id} className="group flex items-center gap-3 px-4 py-2 text-sm">
                  <FavoriteStar
                    active={d.is_favorite}
                    onToggle={() => void patchDevice(d, { is_favorite: !d.is_favorite })}
                  />
                  <span
                    title={d.online ? "접속 중" : "꺼짐"}
                    className={`inline-block h-2 w-2 shrink-0 rounded-full ${d.online ? "bg-[color:var(--success)]" : "bg-muted-token"}`}
                  />
                  <div className={`min-w-0 flex-1 ${d.is_hidden ? "opacity-50" : ""}`}>
                    {editing?.id === d.id ? (
                      <input
                        className="input h-8 w-full"
                        autoFocus
                        maxLength={100}
                        value={editing.name}
                        onChange={(e) => setEditing({ id: d.id, name: e.target.value })}
                        onBlur={() => void submitRename()}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void submitRename();
                          if (e.key === "Escape") setEditing(null);
                        }}
                      />
                    ) : (
                      <button
                        className="block max-w-full truncate text-left font-medium hover:underline"
                        title="눌러서 이름 바꾸기"
                        onClick={() => setEditing({ id: d.id, name: d.name })}
                      >
                        {d.name}
                        {d.id === deviceId && (
                          <span className="ml-2 text-xs text-accent">이 브라우저</span>
                        )}
                      </button>
                    )}
                    <div className="text-xs text-muted">
                      {PLATFORM_LABEL[d.platform] ?? d.platform}
                      {d.last_seen_at && ` · 마지막 접속 ${formatDateTime(d.last_seen_at)}`}
                    </div>
                  </div>
                  <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted">
                    <input
                      type="checkbox"
                      checked={!d.is_hidden}
                      onChange={() => void patchDevice(d, { is_hidden: !d.is_hidden })}
                    />
                    보낼 곳에 표시
                  </label>
                  <button className="btn btn-ghost text-danger" onClick={() => void onDelete(d)}>
                    삭제
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}
