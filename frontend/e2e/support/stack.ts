import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * E2E 실행 동안만 백엔드의 rate limiting 을 끄는 스택 제어 헬퍼.
 *
 * **왜 필요한가.** 한 계정이 짧은 시간에 여러 번 업로드하는 테스트가 여럿이라
 * `rate_limit_upload_per_min = 10`(user 당 60초)에 걸린다. 걸리면 `POST /api/files/batch` 가
 * 429 를 돌려주고 화면에는 "완료 토스트가 안 뜬다"로 나타나므로, 코드 회귀와 구분되지 않는
 * 실패가 된다(실측: 전체 실행에서 folder-upload 2건 + sse 1건). 격리 실행하면 통과한다.
 *
 * **왜 restart 인가.** 설정은 pydantic-settings 가 **기동 시** 읽는다. compose 가 이미
 * `RATE_LIMIT_ENABLED: ${RATE_LIMIT_ENABLED:-true}` 로 호스트 env 를 통과시키므로, 그 값을 주고
 * backend 컨테이너만 다시 띄우면 된다(entrypoint 의 alembic 포함 몇 초).
 *
 * **원복이 계약이다.** 끝나면 원래 값으로 되돌린다. 되돌리기가 실패하면 **크게 경고**한다 —
 * rate limiting 이 꺼진 채로 남은 스택은 개발용이라도 약해진 상태이고, 조용히 남으면 아무도
 * 모른다. 프로세스가 SIGKILL 되면 teardown 이 못 도는데, 그때는 남은 상태 파일이 흔적이 된다.
 *
 * docker/compose 를 못 쓰는 환경(원격 스택 대상 실행 등)에서는 **경고만 하고 넘어간다.**
 * 그 경우 결과는 지금과 같다(429 로 몇 건 실패). 테스트 실행 자체를 막지는 않는다.
 */

// 이 파일은 ESM 으로 로드된다(package.json 의 type: module) — __dirname 이 없다.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../../..");
const PROJECT = process.env.E2E_COMPOSE_PROJECT ?? "mini-drive";
const FLAG = "RATE_LIMIT_ENABLED";
// 상태 파일은 gitignore 된 e2e/.auth/ 에 둔다(auth storageState 와 같은 자리).
const STATE_FILE = path.resolve(HERE, "../.auth/.rate-limit-state.json");

interface SavedState {
  /** 우리가 값을 바꿨는가. false 면 teardown 이 할 일이 없다. */
  changed: boolean;
  /** 바꾸기 전 컨테이너가 갖고 있던 값. */
  previous: string;
}

function compose(args: string[], env?: Record<string, string>): string {
  return execFileSync("docker", ["compose", "-p", PROJECT, ...args], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, ...env },
    timeout: 180_000,
  });
}

/** 실행 중인 backend 컨테이너가 지금 갖고 있는 플래그 값. 읽을 수 없으면 null. */
function currentFlag(): string | null {
  try {
    // printenv 는 값이 없으면 exit 1 이라 || true 로 감싼다 — 미설정도 정상 상태다.
    const out = compose(["exec", "-T", "backend", "sh", "-c", `printenv ${FLAG} || true`]);
    return out.trim();
  } catch {
    return null;
  }
}

/** backend 를 주어진 플래그 값으로 다시 띄우고 응답할 때까지 기다린다. */
function restartBackend(value: string): void {
  compose(["up", "-d", "backend"], { [FLAG]: value });
  waitHealthy();
}

function waitHealthy(): void {
  const base = process.env.E2E_BASE_URL ?? "http://localhost";
  const deadline = Date.now() + 90_000;
  let last = "";
  while (Date.now() < deadline) {
    try {
      // 무인증 엔드포인트라 토큰이 필요 없다.
      const code = execFileSync(
        "curl",
        ["-s", "-o", "/dev/null", "-w", "%{http_code}", `${base}/api/setup/status`],
        { encoding: "utf8", timeout: 10_000 },
      ).trim();
      if (code === "200") return;
      last = `HTTP ${code}`;
    } catch (e) {
      last = e instanceof Error ? e.message : String(e);
    }
    // busy-wait 대신 짧게 잠든다(동기 컨텍스트라 Atomics.wait 을 쓴다).
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000);
  }
  throw new Error(`backend 가 90초 안에 응답하지 않았다 (마지막: ${last})`);
}

/** 실행 동안 rate limiting 을 끈다. 이미 꺼져 있으면 아무것도 하지 않는다. */
export function disableRateLimit(): void {
  const previous = currentFlag();
  if (previous === null) {
    console.warn(
      `[e2e] docker compose -p ${PROJECT} 에 접근할 수 없어 rate limiting 을 그대로 둔다.\n` +
        "       업로드가 많은 테스트가 429 로 실패할 수 있다.",
    );
    return;
  }
  if (previous.toLowerCase() === "false") {
    /*
      이미 꺼져 있다. 두 경우가 섞여 있어 구분해야 한다 —
        ① 사용자가 직접 끈 스택: 우리가 되돌릴 것이 없다.
        ② **이전 실행이 끄고 원복하지 못한 상태**(SIGKILL 등): 그 실행이 남긴 상태 파일에
           원래 값이 들어 있다. 여기서 `changed: false` 로 덮으면 그 원복 대상을 잃고
           스택이 영구히 rate limiting 없이 남는다.
      그래서 남아 있는 미완 상태(changed: true)는 **보존한다.**
    */
    const leftover = load();
    if (leftover?.changed) {
      console.warn(
        "[e2e] 이전 실행이 원복하지 못한 상태를 발견했다 " +
          `(${FLAG}=${leftover.previous || "미설정"} 으로 되돌릴 예정). 이번 teardown 이 처리한다.`,
      );
      return;
    }
    save({ changed: false, previous });
    console.log("[e2e] rate limiting 이 이미 꺼져 있다 — 그대로 쓴다.");
    return;
  }

  console.log(`[e2e] rate limiting 을 끈다 (${FLAG}=${previous || "미설정"} → false), backend 재기동...`);
  // 먼저 기록한다 — 재기동 도중 죽어도 원복 대상이 남는다.
  save({ changed: true, previous });
  restartBackend("false");
  console.log("[e2e] backend 준비 완료 (rate limiting off).");
}

/** 원래 값으로 되돌린다. 실패하면 사람이 볼 수 있게 크게 경고한다. */
export function restoreRateLimit(): void {
  const state = load();
  if (state === null || !state.changed) {
    clear();
    return;
  }
  // 미설정이었다면 compose 기본값(true)으로 되돌린다 — "운영 안전" 기본을 그대로 복원한다.
  const target = state.previous || "true";
  try {
    console.log(`[e2e] rate limiting 을 되돌린다 (${FLAG}=${target}), backend 재기동...`);
    restartBackend(target);
    clear();
    console.log("[e2e] backend 준비 완료 (rate limiting 원복).");
  } catch (e) {
    console.error(
      "\n" +
        "!!!! [e2e] rate limiting 원복에 실패했다 — 스택이 rate limiting 없이 떠 있다.\n" +
        `!!!!       직접 되돌려라:  ${FLAG}=${target} docker compose -p ${PROJECT} up -d backend\n` +
        `!!!!       원인: ${e instanceof Error ? e.message : String(e)}\n`,
    );
  }
}

function save(state: SavedState): void {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state));
}

function load(): SavedState | null {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) as SavedState;
  } catch {
    return null;
  }
}

function clear(): void {
  try {
    fs.unlinkSync(STATE_FILE);
  } catch {
    /* 없으면 지울 것도 없다 */
  }
}
