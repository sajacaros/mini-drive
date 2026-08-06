/**
 * 그룹 게시판 API (spec/group-board.md).
 *
 * 접근 판정은 전부 서버가 한다 — 목록은 **내가 접근 가능한 게시판만** 오고, 없는 것은 이름조차
 * 오지 않는다. 프런트는 응답의 `permission` / `can_edit` / `can_delete` 를 그대로 믿고 버튼만
 * 감춘다(막는 쪽은 언제나 서버다).
 */

import apiClient from "./client";
import type {
  AdminBoardListResponse,
  Board,
  BoardAttachment,
  BoardComment,
  BoardCommentListResponse,
  BoardGroupGrant,
  BoardGroupListResponse,
  BoardListResponse,
  BoardPermission,
  BoardPostDetail,
  BoardPostListResponse,
  AdminBoard,
} from "./types";

// --- 게시판 ------------------------------------------------------------------

export async function listBoards(): Promise<BoardListResponse> {
  const { data } = await apiClient.get<BoardListResponse>("/boards");
  return data;
}

export async function getBoard(boardId: number): Promise<Board> {
  const { data } = await apiClient.get<Board>(`/boards/${boardId}`);
  return data;
}

// --- 글 ----------------------------------------------------------------------

export async function listPosts(
  boardId: number,
  page = 1,
  size = 20,
): Promise<BoardPostListResponse> {
  const { data } = await apiClient.get<BoardPostListResponse>(
    `/boards/${boardId}/posts`,
    { params: { page, size } },
  );
  return data;
}

export async function getPost(boardId: number, postId: number): Promise<BoardPostDetail> {
  const { data } = await apiClient.get<BoardPostDetail>(
    `/boards/${boardId}/posts/${postId}`,
  );
  return data;
}

export async function createPost(
  boardId: number,
  title: string,
  body: string,
): Promise<BoardPostDetail> {
  const { data } = await apiClient.post<BoardPostDetail>(`/boards/${boardId}/posts`, {
    title,
    body,
  });
  return data;
}

export async function updatePost(
  boardId: number,
  postId: number,
  payload: { title?: string; body?: string },
): Promise<BoardPostDetail> {
  const { data } = await apiClient.patch<BoardPostDetail>(
    `/boards/${boardId}/posts/${postId}`,
    payload,
  );
  return data;
}

export async function deletePost(boardId: number, postId: number): Promise<void> {
  await apiClient.delete(`/boards/${boardId}/posts/${postId}`);
}

// --- 댓글 --------------------------------------------------------------------

export async function listComments(
  boardId: number,
  postId: number,
): Promise<BoardCommentListResponse> {
  const { data } = await apiClient.get<BoardCommentListResponse>(
    `/boards/${boardId}/posts/${postId}/comments`,
  );
  return data;
}

export async function createComment(
  boardId: number,
  postId: number,
  body: string,
): Promise<BoardComment> {
  const { data } = await apiClient.post<BoardComment>(
    `/boards/${boardId}/posts/${postId}/comments`,
    { body },
  );
  return data;
}

export async function deleteComment(
  boardId: number,
  postId: number,
  commentId: number,
): Promise<void> {
  await apiClient.delete(`/boards/${boardId}/posts/${postId}/comments/${commentId}`);
}

// --- 첨부 --------------------------------------------------------------------

export async function uploadAttachment(
  boardId: number,
  postId: number,
  file: File,
): Promise<BoardAttachment> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<BoardAttachment>(
    `/boards/${boardId}/posts/${postId}/attachments`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

export async function deleteAttachment(
  boardId: number,
  postId: number,
  attachmentId: number,
): Promise<void> {
  await apiClient.delete(
    `/boards/${boardId}/posts/${postId}/attachments/${attachmentId}`,
  );
}

/**
 * 첨부 다운로드.
 *
 * 드라이브의 티켓 방식(무헤더 네비게이션)을 쓰지 않고 인증된 XHR 로 받아 blob 으로 저장한다.
 * 게시판 첨부는 파일당 10MB 상한이라 메모리에 한 번 올려도 되고, 티켓 발급 엔드포인트를 하나 더
 * 두는 것보다 이쪽이 붙는 면이 좁다. 응답은 nginx 가 X-Accel-Redirect 로 스트리밍한 본문이다.
 */
export async function downloadAttachment(
  boardId: number,
  postId: number,
  attachment: BoardAttachment,
): Promise<void> {
  const { data } = await apiClient.get<Blob>(
    `/boards/${boardId}/posts/${postId}/attachments/${attachment.id}/download`,
    { responseType: "blob" },
  );
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = attachment.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- 관리자 ------------------------------------------------------------------

export async function adminListBoards(): Promise<AdminBoardListResponse> {
  const { data } = await apiClient.get<AdminBoardListResponse>("/admin/boards");
  return data;
}

export async function adminCreateBoard(
  name: string,
  description: string | null,
): Promise<AdminBoard> {
  const { data } = await apiClient.post<AdminBoard>("/admin/boards", {
    name,
    description,
  });
  return data;
}

export async function adminUpdateBoard(
  boardId: number,
  payload: { name?: string; description?: string | null },
): Promise<AdminBoard> {
  const { data } = await apiClient.patch<AdminBoard>(`/admin/boards/${boardId}`, payload);
  return data;
}

export async function adminDeleteBoard(boardId: number): Promise<void> {
  await apiClient.delete(`/admin/boards/${boardId}`);
}

export async function adminListBoardGroups(
  boardId: number,
): Promise<BoardGroupListResponse> {
  const { data } = await apiClient.get<BoardGroupListResponse>(
    `/admin/boards/${boardId}/groups`,
  );
  return data;
}

export async function adminGrantBoardGroup(
  boardId: number,
  groupId: number,
  permission: BoardPermission,
): Promise<BoardGroupGrant> {
  const { data } = await apiClient.post<BoardGroupGrant>(
    `/admin/boards/${boardId}/groups`,
    { group_id: groupId, permission },
  );
  return data;
}

export async function adminRevokeBoardGroup(
  boardId: number,
  groupId: number,
): Promise<void> {
  await apiClient.delete(`/admin/boards/${boardId}/groups/${groupId}`);
}
