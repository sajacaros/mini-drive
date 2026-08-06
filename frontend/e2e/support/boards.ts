import type { Page } from "@playwright/test";

/**
 * 게시판 E2E 공용 시드 헬퍼 (spec/group-board.md).
 *
 * 글·댓글·첨부 축을 보려면 먼저 **열린 게시판**이 있어야 한다 — 게시판을 만들고 그룹을 붙이는
 * 두 단계다. 그걸 매번 관리 화면 UI 로 되풀이하면 검증하려는 것(글쓰기)과 상관없는 클릭이
 * 스펙마다 열 줄씩 붙으므로, **준비 단계만** API 로 시드한다(검증은 전부 UI 로 한다).
 * 관리 화면 자체의 UI 는 `admin-open-and-close.spec.ts` 가 클릭으로 본다.
 *
 * 붙이는 그룹은 `@전사` 시스템 그룹이다. 멤버십이 물질화되지 않고 활성 사용자에게 자동으로
 * 끼워지므로(services/groups.py 의 get_user_group_ids), e2e admin 을 그룹에 가입시키는
 * 단계 없이 write 가 선다.
 *
 * 토큰은 로그인 상태의 localStorage 에서 읽는다 — auth.setup 이 저장한 storageState 와
 * 동일한 세션이라 별도 로그인이 필요 없다(support/shares.ts 와 같은 방식).
 */

const TOKEN_KEY = "minidrive.access_token";

/** `@전사` 시스템 그룹 이름. services/groups.py 의 ALL_USERS_GROUP_NAME 과 같아야 한다. */
export const ALL_USERS_GROUP = "@전사";

/**
 * 게시판을 만들고 `@전사` 에 write 로 연다. 반환: 만들어진 게시판 id.
 *
 * write 로 여는 이유는 이 헬퍼를 쓰는 스펙이 전부 글·댓글을 남기기 때문이다. read 만 필요한
 * 축(버튼이 감춰지는가)은 관리 화면 스펙이 UI 로 권한을 바꿔 가며 본다.
 */
export async function seedOpenBoard(page: Page, name: string): Promise<number> {
  return page.evaluate(
    async ({ tokenKey, boardName, groupName }) => {
      const token = localStorage.getItem(tokenKey);
      const auth = { Authorization: `Bearer ${token}` };
      const json = { ...auth, "Content-Type": "application/json" };

      const created = await fetch("/api/admin/boards", {
        method: "POST",
        headers: json,
        body: JSON.stringify({ name: boardName, description: "e2e 시드 게시판" }),
      });
      if (!created.ok) throw new Error(`게시판 생성 실패: ${created.status} ${await created.text()}`);
      const boardId = (await created.json()).id as number;

      const groups = await fetch("/api/groups?page=1&size=100", { headers: auth });
      if (!groups.ok) throw new Error(`그룹 목록 실패: ${groups.status} ${await groups.text()}`);
      const all = (await groups.json()).items as Array<{ id: number; name: string }>;
      const target = all.find((g) => g.name === groupName);
      if (!target) throw new Error(`${groupName} 그룹을 찾지 못했습니다.`);

      const grant = await fetch(`/api/admin/boards/${boardId}/groups`, {
        method: "POST",
        headers: json,
        body: JSON.stringify({ group_id: target.id, permission: "write" }),
      });
      if (!grant.ok) throw new Error(`그룹 할당 실패: ${grant.status} ${await grant.text()}`);

      return boardId;
    },
    { tokenKey: TOKEN_KEY, boardName: name, groupName: ALL_USERS_GROUP },
  );
}

/**
 * 시드 게시판을 지운다(best-effort, 멱등).
 *
 * 게시판 삭제가 그 아래 글·댓글·첨부를 한 번에 잠그고 첨부 오브젝트까지 회수하므로
 * (spec/group-board.md 「회수는 삭제하는 그 자리에서 한다」) 개별 정리가 필요 없다.
 */
export async function purgeBoard(page: Page, boardId: number): Promise<void> {
  await page.evaluate(
    async ({ tokenKey, id }) => {
      const auth = { Authorization: `Bearer ${localStorage.getItem(tokenKey)}` };
      await fetch(`/api/admin/boards/${id}`, { method: "DELETE", headers: auth });
    },
    { tokenKey: TOKEN_KEY, id: boardId },
  );
}
