import type { Page } from "@playwright/test";

/**
 * 공유 링크 E2E 공용 시드 헬퍼.
 *
 * 공유 목록의 탭/페이지네이션을 검증하려면 한 계정에 20건 넘는 공유가 있어야 한다. 그걸
 * UI(업로드 → 공유 모달)로 만들면 스위트가 몇 분씩 늘어나므로, **준비 단계만** 브라우저
 * 컨텍스트 안에서 API 로 직접 시드한다(검증은 전부 UI 로 한다).
 *
 * 토큰은 로그인 상태의 localStorage 에서 읽는다 — auth.setup 이 저장한 storageState 와
 * 동일한 세션이라 별도 로그인이 필요 없다.
 */

const TOKEN_KEY = "minidrive.access_token";

/** 파일 1개를 올리고 그 파일로 공유 링크 count 개를 만든다. 반환: 업로드한 파일 id. */
export async function seedShares(page: Page, name: string, count: number): Promise<number> {
  return page.evaluate(
    async ({ tokenKey, fileName, n }) => {
      const token = localStorage.getItem(tokenKey);
      const auth = { Authorization: `Bearer ${token}` };

      const form = new FormData();
      form.append("file", new Blob([`e2e shares seed ${fileName}`]), fileName);
      const up = await fetch("/api/files/upload", { method: "POST", headers: auth, body: form });
      if (!up.ok) throw new Error(`업로드 실패: ${up.status} ${await up.text()}`);
      const fileId = (await up.json()).id as number;

      // 한 파일에 여러 공유 링크를 걸 수 있다 — 목록 건수를 늘리는 가장 싼 방법.
      for (let i = 0; i < n; i += 1) {
        const res = await fetch("/api/shares", {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json" },
          body: JSON.stringify({ file_id: fileId, permission: "download" }),
        });
        if (!res.ok) throw new Error(`공유 생성 실패: ${res.status} ${await res.text()}`);
      }
      return fileId;
    },
    { tokenKey: TOKEN_KEY, fileName: name, n: count },
  );
}

/**
 * 파일 1개를 올리고 그 파일로 공유 링크 1개를 만든다. 반환: 파일 id + 공개 URL.
 * 권한(read/download)에 따라 공개 화면이 어떻게 달라지는지 검증할 때 쓴다.
 */
export async function seedShareLink(
  page: Page,
  name: string,
  permission: "read" | "download",
  body: string,
): Promise<{ fileId: number; shareUrl: string }> {
  return page.evaluate(
    async ({ tokenKey, fileName, perm, content }) => {
      const token = localStorage.getItem(tokenKey);
      const auth = { Authorization: `Bearer ${token}` };

      const form = new FormData();
      // 미리보기가 실제로 열려야 하므로 text/plain 으로 올린다(백엔드는 클라이언트 MIME 을 믿는다).
      form.append("file", new Blob([content], { type: "text/plain" }), fileName);
      const up = await fetch("/api/files/upload", { method: "POST", headers: auth, body: form });
      if (!up.ok) throw new Error(`업로드 실패: ${up.status} ${await up.text()}`);
      const fileId = (await up.json()).id as number;

      const res = await fetch("/api/shares", {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: fileId, permission: perm }),
      });
      if (!res.ok) throw new Error(`공유 생성 실패: ${res.status} ${await res.text()}`);
      return { fileId, shareUrl: (await res.json()).share_url as string };
    },
    { tokenKey: TOKEN_KEY, fileName: name, perm: permission, content: body },
  );
}

/**
 * 시드 파일을 영구 삭제한다(best-effort). 파일을 영구 삭제하면 그 파일의 shares 행도 함께
 * 지워지므로 공유 목록까지 한 번에 정리된다.
 */
export async function purgeSeededFile(page: Page, fileId: number): Promise<void> {
  await page.evaluate(
    async ({ tokenKey, id }) => {
      const auth = { Authorization: `Bearer ${localStorage.getItem(tokenKey)}` };
      await fetch(`/api/files/${id}/delete`, { method: "POST", headers: auth });
      await fetch(`/api/files/${id}/permanent-delete`, { method: "POST", headers: auth });
    },
    { tokenKey: TOKEN_KEY, id: fileId },
  );
}
