"""그룹 게시판 API 요청·응답 스키마 (spec/group-board.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import BoardPermission

# --- 게시판 ------------------------------------------------------------------


class BoardCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class BoardUpdateRequest(BaseModel):
    """부분 수정 — 주지 않은 필드는 그대로 둔다."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class BoardResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    #: 내 유효 권한. 관리자가 그룹 할당 없이 열람 중이면 None 이다.
    permission: BoardPermission | None = None
    post_count: int = 0


class BoardListResponse(BaseModel):
    items: list[BoardResponse]


class AdminBoardResponse(BaseModel):
    """관리 화면용 — 할당 그룹 수·글 수를 함께 준다."""

    id: int
    name: str
    description: str | None
    created_at: datetime
    group_count: int
    post_count: int


class AdminBoardListResponse(BaseModel):
    items: list[AdminBoardResponse]


# --- 그룹 할당 ---------------------------------------------------------------


class BoardGroupGrantRequest(BaseModel):
    group_id: int
    permission: BoardPermission = BoardPermission.READ


class BoardGroupResponse(BaseModel):
    group_id: int
    group_name: str
    permission: BoardPermission
    granted_at: datetime


class BoardGroupListResponse(BaseModel):
    items: list[BoardGroupResponse]


# --- 글 ----------------------------------------------------------------------


class PostCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = ""


class PostUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = None


class AttachmentResponse(BaseModel):
    id: int
    filename: str
    mime_type: str | None
    size: int
    uploaded_at: datetime


class PostSummaryResponse(BaseModel):
    id: int
    title: str
    author_id: int
    author_name: str
    comment_count: int
    attachment_count: int
    created_at: datetime
    updated_at: datetime


class PostListResponse(BaseModel):
    items: list[PostSummaryResponse]
    total: int
    page: int
    size: int


class PostDetailResponse(BaseModel):
    id: int
    board_id: int
    title: str
    body: str
    author_id: int
    author_name: str
    attachments: list[AttachmentResponse]
    #: 수정은 작성자 본인만, 삭제는 작성자·관리자 — 프런트가 버튼을 감추는 근거다.
    can_edit: bool
    can_delete: bool
    created_at: datetime
    updated_at: datetime


# --- 댓글 --------------------------------------------------------------------


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class CommentResponse(BaseModel):
    id: int
    author_id: int
    author_name: str
    body: str
    #: 삭제된 댓글은 자리만 남는다 — body 가 안내 문구로 바뀌고 이 값이 True 다.
    is_deleted: bool
    can_delete: bool
    created_at: datetime


class CommentListResponse(BaseModel):
    items: list[CommentResponse]
