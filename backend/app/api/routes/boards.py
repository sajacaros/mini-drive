"""그룹 게시판 라우터 (spec/group-board.md).

라우터가 둘이다:
  - `router` — `/api/boards`. 일반 사용자 경로이고 인가는 전부 `ensure_board_access` 가 한다.
  - `admin_router` — `/api/admin/boards`. 게시판 생성·수정·삭제·그룹 할당이며 라우터 전체에
    `require_admin` 을 건다(admin.py 와 같은 방식).

게시판별 운영자 역할이 없다는 결정이 이 분리로 드러난다 — 관리 동작은 전부 admin 라우터 한쪽에
모이고, 사용자 라우터에는 "이 게시판의 관리자인가?" 같은 판정이 아예 없다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi import File as FileParam

from app.api.deps import AdminUser, CurrentUser, DbSession, require_admin
from app.api.download import gateway_download_response
from app.schemas.boards import (
    AdminBoardListResponse,
    AdminBoardResponse,
    AttachmentResponse,
    BoardCreateRequest,
    BoardGroupGrantRequest,
    BoardGroupListResponse,
    BoardGroupResponse,
    BoardListResponse,
    BoardResponse,
    BoardUpdateRequest,
    CommentCreateRequest,
    CommentListResponse,
    CommentResponse,
    PostCreateRequest,
    PostDetailResponse,
    PostListResponse,
    PostSummaryResponse,
    PostUpdateRequest,
)
from app.services import boards as boards_service
from app.services.boards import BoardServiceError, CommentView, PostDetail, PostSummary
from app.services.storage import get_storage

router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_admin)])

# 삭제된 댓글이 남긴 자리에 채우는 문구. 본문은 응답에서 지워지고 이 문자열만 나간다 —
# 원문을 프런트로 흘려보내지 않으려면 갈아 끼우는 지점이 한 곳이어야 한다.
_DELETED_COMMENT_BODY = "삭제된 댓글입니다."


def _http_error(exc: BoardServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _post_summary(item: PostSummary) -> PostSummaryResponse:
    return PostSummaryResponse(
        id=item.post.id,
        title=item.post.title,
        author_id=item.post.author_id,
        author_name=item.author_name,
        comment_count=item.comment_count,
        attachment_count=item.attachment_count,
        created_at=item.post.created_at,
        updated_at=item.post.updated_at,
    )


def _post_detail(detail: PostDetail) -> PostDetailResponse:
    return PostDetailResponse(
        id=detail.post.id,
        board_id=detail.post.board_id,
        title=detail.post.title,
        body=detail.post.body,
        author_id=detail.post.author_id,
        author_name=detail.author_name,
        attachments=[
            AttachmentResponse(
                id=a.id,
                filename=a.filename,
                mime_type=a.mime_type,
                size=a.size,
                uploaded_at=a.uploaded_at,
            )
            for a in detail.attachments
        ],
        can_edit=detail.can_edit,
        can_delete=detail.can_delete,
        created_at=detail.post.created_at,
        updated_at=detail.post.updated_at,
    )


def _comment_response(view: CommentView) -> CommentResponse:
    return CommentResponse(
        id=view.comment.id,
        author_id=view.comment.author_id,
        author_name=view.author_name,
        body=_DELETED_COMMENT_BODY if view.comment.is_deleted else view.comment.body,
        is_deleted=view.comment.is_deleted,
        can_delete=view.can_delete,
        created_at=view.comment.created_at,
    )


# --- 사용자 경로 -------------------------------------------------------------


@router.get("", response_model=BoardListResponse)
async def list_boards(user: CurrentUser, session: DbSession) -> BoardListResponse:
    """**내가 접근 가능한 게시판만** 돌려준다. 접근 없는 게시판은 이름조차 나오지 않는다."""
    summaries = await boards_service.list_accessible_boards(session, user)
    return BoardListResponse(
        items=[
            BoardResponse(
                id=s.board.id,
                name=s.board.name,
                description=s.board.description,
                created_at=s.board.created_at,
                permission=s.permission,
                post_count=s.post_count,
            )
            for s in summaries
        ]
    )


@router.get("/{board_id}", response_model=BoardResponse)
async def get_board(board_id: int, user: CurrentUser, session: DbSession) -> BoardResponse:
    """게시판 하나 (내 유효 권한 포함). 접근 불가는 404."""
    try:
        access = await boards_service.ensure_board_access(session, user, board_id, "read")
        _groups, post_count = await boards_service.board_counts(session, board_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return BoardResponse(
        id=access.board.id,
        name=access.board.name,
        description=access.board.description,
        created_at=access.board.created_at,
        permission=access.permission,
        post_count=post_count,
    )


@router.get("/{board_id}/posts", response_model=PostListResponse)
async def list_posts(
    board_id: int,
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PostListResponse:
    """글 목록 (최신순). read 필요."""
    try:
        items, total = await boards_service.list_posts(session, user, board_id, page, size)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return PostListResponse(
        items=[_post_summary(i) for i in items], total=total, page=page, size=size
    )


@router.post(
    "/{board_id}/posts",
    response_model=PostDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    board_id: int, payload: PostCreateRequest, user: CurrentUser, session: DbSession
) -> PostDetailResponse:
    """글 작성. write 필요 (read 만 있으면 403)."""
    try:
        post = await boards_service.create_post(
            session, user, board_id, payload.title, payload.body
        )
        # 방금 만든 글을 상세 조회로 다시 읽는다 — 작성자 이름·can_edit/can_delete 판정이
        # get_post 한 곳에서만 나오게 해 응답 조립을 두 벌 두지 않는다.
        detail = await boards_service.get_post(session, user, board_id, post.id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return _post_detail(detail)


@router.get("/{board_id}/posts/{post_id}", response_model=PostDetailResponse)
async def get_post(
    board_id: int, post_id: int, user: CurrentUser, session: DbSession
) -> PostDetailResponse:
    """글 상세 (첨부 포함). read 필요."""
    try:
        detail = await boards_service.get_post(session, user, board_id, post_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return _post_detail(detail)


@router.patch("/{board_id}/posts/{post_id}", response_model=PostDetailResponse)
async def update_post(
    board_id: int,
    post_id: int,
    payload: PostUpdateRequest,
    user: CurrentUser,
    session: DbSession,
) -> PostDetailResponse:
    """글 수정 — 작성자 본인만. 관리자도 남의 글은 고치지 못한다."""
    try:
        await boards_service.update_post(
            session, user, board_id, post_id, title=payload.title, body=payload.body
        )
        detail = await boards_service.get_post(session, user, board_id, post_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return _post_detail(detail)


@router.delete("/{board_id}/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    board_id: int, post_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """글 삭제 — 작성자 본인 또는 관리자. 첨부 오브젝트를 그 자리에서 회수한다."""
    try:
        await boards_service.delete_post(
            session, get_storage(), user, board_id, post_id
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- 댓글 --------------------------------------------------------------------


@router.get("/{board_id}/posts/{post_id}/comments", response_model=CommentListResponse)
async def list_comments(
    board_id: int, post_id: int, user: CurrentUser, session: DbSession
) -> CommentListResponse:
    """댓글 목록 (오래된 순). read 필요. 삭제된 댓글은 자리만 남는다."""
    try:
        views = await boards_service.list_comments(session, user, board_id, post_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return CommentListResponse(items=[_comment_response(v) for v in views])


@router.post(
    "/{board_id}/posts/{post_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    board_id: int,
    post_id: int,
    payload: CommentCreateRequest,
    user: CurrentUser,
    session: DbSession,
) -> CommentResponse:
    """댓글 작성. write 필요."""
    try:
        comment = await boards_service.create_comment(
            session, user, board_id, post_id, payload.body
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return CommentResponse(
        id=comment.id,
        author_id=comment.author_id,
        author_name=user.display_name,
        body=comment.body,
        is_deleted=False,
        can_delete=True,
        created_at=comment.created_at,
    )


@router.delete(
    "/{board_id}/posts/{post_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_comment(
    board_id: int,
    post_id: int,
    comment_id: int,
    user: CurrentUser,
    session: DbSession,
) -> Response:
    """댓글 삭제 — 작성자 본인 또는 관리자. 소프트 삭제(자리는 남는다)."""
    try:
        await boards_service.delete_comment(
            session, user, board_id, post_id, comment_id
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- 첨부 --------------------------------------------------------------------


@router.post(
    "/{board_id}/posts/{post_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_attachment(
    board_id: int,
    post_id: int,
    user: CurrentUser,
    session: DbSession,
    file: Annotated[UploadFile, FileParam(...)],
) -> AttachmentResponse:
    """첨부 업로드 (단일 요청 multipart). write + 작성자 본인.

    10 MB 상한이라 재개 가능 업로드·멀티파트가 필요 없다. 개인 할당량은 차감하지 않는다.
    """
    try:
        attachment = await boards_service.add_attachment(
            session, get_storage(), user, board_id, post_id, file
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size=attachment.size,
        uploaded_at=attachment.uploaded_at,
    )


@router.delete(
    "/{board_id}/posts/{post_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    board_id: int,
    post_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
) -> Response:
    """첨부 개별 제거 — 작성자 또는 관리자. 오브젝트를 그 자리에서 회수한다."""
    try:
        await boards_service.delete_attachment(
            session, get_storage(), user, board_id, post_id, attachment_id
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{board_id}/posts/{post_id}/attachments/{attachment_id}/download")
async def download_attachment(
    board_id: int,
    post_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
) -> Response:
    """첨부 다운로드. read 필요. 드라이브와 같은 X-Accel-Redirect 게이트웨이 스트리밍이다."""
    try:
        internal, filename, mime = await boards_service.prepare_attachment_download(
            session, get_storage(), user, board_id, post_id, attachment_id
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return gateway_download_response(internal, filename, mime)


# --- 관리자 경로 -------------------------------------------------------------


@admin_router.get("", response_model=AdminBoardListResponse)
async def admin_list_boards(
    admin: AdminUser, session: DbSession
) -> AdminBoardListResponse:
    """전체 게시판 목록 (할당 그룹 수·글 수 포함)."""
    rows = await boards_service.list_all_boards(session)
    return AdminBoardListResponse(
        items=[
            AdminBoardResponse(
                id=b.id,
                name=b.name,
                description=b.description,
                created_at=b.created_at,
                group_count=group_count,
                post_count=post_count,
            )
            for b, group_count, post_count in rows
        ]
    )


@admin_router.post(
    "", response_model=AdminBoardResponse, status_code=status.HTTP_201_CREATED
)
async def admin_create_board(
    payload: BoardCreateRequest, admin: AdminUser, session: DbSession
) -> AdminBoardResponse:
    """게시판 생성. 활성 게시판 사이에서 이름이 겹치면 409."""
    try:
        board = await boards_service.create_board(
            session, admin, payload.name, payload.description
        )
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return AdminBoardResponse(
        id=board.id,
        name=board.name,
        description=board.description,
        created_at=board.created_at,
        group_count=0,
        post_count=0,
    )


@admin_router.patch("/{board_id}", response_model=AdminBoardResponse)
async def admin_update_board(
    board_id: int,
    payload: BoardUpdateRequest,
    admin: AdminUser,
    session: DbSession,
) -> AdminBoardResponse:
    """게시판 이름·설명 수정."""
    try:
        board = await boards_service.update_board(
            session, admin, board_id, name=payload.name, description=payload.description
        )
        group_count, post_count = await boards_service.board_counts(session, board_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return AdminBoardResponse(
        id=board.id,
        name=board.name,
        description=board.description,
        created_at=board.created_at,
        group_count=group_count,
        post_count=post_count,
    )


@admin_router.delete("/{board_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_board(
    board_id: int, admin: AdminUser, session: DbSession
) -> Response:
    """게시판 삭제(소프트). 하위 글의 첨부 오브젝트는 이 요청 안에서 회수된다."""
    try:
        await boards_service.delete_board(session, get_storage(), admin, board_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("/{board_id}/groups", response_model=BoardGroupListResponse)
async def admin_list_board_groups(
    board_id: int, admin: AdminUser, session: DbSession
) -> BoardGroupListResponse:
    """게시판에 할당된 그룹 목록."""
    try:
        rows = await boards_service.list_board_groups(session, board_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return BoardGroupListResponse(
        items=[
            BoardGroupResponse(
                group_id=bg.group_id,
                group_name=name,
                permission=bg.permission,
                granted_at=bg.granted_at,
            )
            for bg, name in rows
        ]
    )


@admin_router.post(
    "/{board_id}/groups",
    response_model=BoardGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_grant_board_group(
    board_id: int,
    payload: BoardGroupGrantRequest,
    admin: AdminUser,
    session: DbSession,
) -> BoardGroupResponse:
    """그룹 할당 (멱등 — 이미 있으면 권한을 갈아끼운다)."""
    try:
        row = await boards_service.grant_board_group(
            session, admin, board_id, payload.group_id, payload.permission
        )
        groups = await boards_service.list_board_groups(session, board_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    name = next((n for bg, n in groups if bg.group_id == row.group_id), "")
    return BoardGroupResponse(
        group_id=row.group_id,
        group_name=name,
        permission=row.permission,
        granted_at=row.granted_at,
    )


@admin_router.delete(
    "/{board_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def admin_revoke_board_group(
    board_id: int, group_id: int, admin: AdminUser, session: DbSession
) -> Response:
    """그룹 할당 회수. 글은 남고 접근만 끊긴다 — 작성자 본인도 예외가 아니다."""
    try:
        await boards_service.revoke_board_group(session, admin, board_id, group_id)
    except BoardServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
