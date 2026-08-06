"""그룹 게시판 서비스 (spec/group-board.md).

**모든 경로가 `ensure_board_access` 하나를 먼저 탄다.** 드라이브의 `ensure_file_access` 에
대응하며 게시판·글·댓글·첨부가 전부 이 관문을 지난다.

    유효 권한 = 내가 속한 그룹 중 이 게시판에 할당된 것들의 최고 수준 (write > read)

상속이 없으므로 판정은 조인 한 번이다 — 드라이브처럼 조상 경로를 recursive CTE 로 훑지도,
Redis 세대 카운터로 캐시를 무효화하지도 않는다. 캐시가 없다는 건 권한 변경이 즉시 반영된다는
뜻이기도 하다.

작성자 예외를 두지 않는 것이 이 모듈의 두 번째 규칙이다. *게시판 접근이 곧 글 접근* 한 줄이라
목록·상세·댓글·첨부·수정·삭제가 전부 같은 판정을 쓴다. 예외를 하나 두면 여섯 자리에서 각각
"본인인가?"를 다시 묻게 되고, 그 중 하나를 빠뜨리는 것이 곧 구멍이다.

관리자(admin/super_admin)는 예외다 — **읽고 지울 수 있으나 쓰지는 못한다**. 드라이브의
"admin 도 파일 내용에 접근하지 못한다"(PRD 3.6.4)를 게시판에 옮기지 않은 이유는 spec
「admin 이 게시판 내용을 보는가」에 있다: 게시판은 개인 공간이 아니라 관리자가 만들고 관리자가
그룹을 붙인 조직의 공용 공간이고, 못 읽는 글을 지우게 하는 것은 성립하지 않는다.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.logging import get_logger
from app.models import (
    AuditLog,
    Board,
    BoardAttachment,
    BoardComment,
    BoardGroup,
    BoardPost,
    Group,
    User,
)
from app.models.enums import ADMIN_ROLES, BoardPermission
from app.services.groups import get_user_group_ids
from app.services.storage import StorageService

_log = get_logger("app.boards")

AccessNeed = Literal["read", "write"]

# 권한 수준 순위 — write > read. 두 단계뿐이라 드라이브의 3단계 _RANK 와 섞이지 않는다.
_RANK: dict[BoardPermission, int] = {
    BoardPermission.READ: 1,
    BoardPermission.WRITE: 2,
}

# 게시판 삭제처럼 키가 많이 모일 수 있는 경로의 delete_many 배치 크기.
_DELETE_CHUNK = 1000


class BoardServiceError(Exception):
    """게시판 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


def _is_admin(user: User) -> bool:
    return user.role in ADMIN_ROLES


# --- 순수 판정 로직 (DB 무관, 단위 테스트 대상) ------------------------------


def permission_covers(effective: BoardPermission, need: AccessNeed) -> bool:
    """effective 권한이 need 를 충족하는지(같거나 높은 수준)."""
    return _RANK[effective] >= _RANK[BoardPermission(need)]


def resolve_effective_permission(
    permissions: list[str] | list[BoardPermission],
) -> BoardPermission | None:
    """할당된 권한들에서 최고 수준을 고른다. 하나도 없으면 None.

    누적(union)이다 — 같은 사람이 그룹A 로 read, 그룹B 로 write 를 받으면 write 다. 드라이브가
    2026-07-28 에 '가장 가까운 조상이 이긴다'에서 누적으로 옮겨 온 것과 같은 결론이되, 여기는
    애초에 거리 개념이 없어 처음부터 누적 말고는 정의할 것이 없다.
    """
    best: BoardPermission | None = None
    for raw in permissions:
        level = BoardPermission(raw)
        if best is None or _RANK[level] > _RANK[best]:
            best = level
    return best


def build_attachment_key(board_id: int, post_id: int) -> str:
    """첨부 오브젝트 키 — `board/{board_id}/{post_id}/{uuid}`.

    드라이브와 같은 버킷을 쓰되 접두사로 가른다. uuid 를 쓰는 이유는 같은 글에 같은 이름을 두
    번 올릴 수 있어서다 — 파일명은 `board_attachments.filename` 이 들고 있고 키는 충돌만
    피하면 된다.
    """
    return f"board/{board_id}/{post_id}/{uuid.uuid4().hex}"


# --- 감사 로그 --------------------------------------------------------------


def _record_audit(
    session: AsyncSession,
    actor_id: int,
    action: str,
    target_id: int,
    detail: dict[str, Any] | None = None,
) -> None:
    """게시판 관련 감사 로그. **열람은 남기지 않는다** — 목록 조회마다 행이 쌓인다.

    남기는 것은 spec 이 정한 일곱 가지다: board.create/update/delete,
    board.group_grant/group_revoke, board.post_delete, board.comment_delete.
    """
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type="board",
            target_id=target_id,
            detail=detail,
        )
    )


# --- 단일 관문 ---------------------------------------------------------------


@dataclass(frozen=True)
class BoardAccess:
    """관문 통과 결과. `permission` 은 그룹 할당으로 얻은 유효 권한(관리자 열람이면 None)."""

    board: Board
    permission: BoardPermission | None
    is_admin: bool

    @property
    def can_write(self) -> bool:
        """글·댓글 작성 가능 여부. 관리자라도 그룹 write 가 없으면 못 쓴다."""
        return self.permission is not None and permission_covers(
            self.permission, "write"
        )


async def get_effective_permission(
    session: AsyncSession, user: User, board_id: int
) -> BoardPermission | None:
    """그룹 할당으로 얻은 유효 권한. 관리자 특권은 여기에 섞지 않는다.

    `get_user_group_ids` 를 그대로 쓴다 — 시스템 그룹(`@전사`)은 멤버십을 물질화하지 않고 그
    함수가 활성 사용자에게 항상 끼워 넣으므로, `@전사 read` 할당도 별도 처리 없이 맞는다.
    """
    group_ids = await get_user_group_ids(session, user.id)
    if not group_ids:
        return None
    rows = (
        await session.execute(
            select(BoardGroup.permission).where(
                BoardGroup.board_id == board_id,
                BoardGroup.group_id.in_(group_ids),
            )
        )
    ).scalars().all()
    return resolve_effective_permission(list(rows))


async def ensure_board_access(
    session: AsyncSession, user: User, board_id: int, need: AccessNeed = "read"
) -> BoardAccess:
    """게시판 접근 권한을 검사하는 단일 관문. 통과하면 BoardAccess 를 반환한다.

    - 접근 불가는 **404**(존재 은닉) — 이름조차 새어 나가지 않는다.
    - 읽기는 되는데 쓰기가 없으면 **403**. 드라이브와 같은 규약이다.
    - 관리자는 read 를 항상 통과하지만 write 는 그룹 할당이 있어야 한다.
    """
    board = await session.get(Board, board_id)
    if board is None or not board.is_active:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")

    admin = _is_admin(user)
    level = await get_effective_permission(session, user, board_id)

    if level is None and not admin:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")

    if need == "write" and (level is None or not permission_covers(level, "write")):
        # 관리자도 여기서 막힌다 — 읽고 지울 수는 있어도 쓰지는 못한다.
        raise BoardServiceError(403, "이 게시판에 글을 쓸 권한이 없습니다.")

    return BoardAccess(board=board, permission=level, is_admin=admin)


async def _get_post(
    session: AsyncSession, board_id: int, post_id: int
) -> BoardPost:
    """삭제되지 않은 글. 없거나 다른 게시판의 글이면 404.

    board_id 를 함께 확인하는 이유는 경로가 `/boards/{id}/posts/{post_id}` 이기 때문이다 —
    접근 가능한 게시판 id 에 남의 게시판 글 id 를 붙여 관문을 우회하는 것을 막는다.
    """
    post = await session.get(BoardPost, post_id)
    if post is None or post.board_id != board_id or post.is_deleted:
        raise BoardServiceError(404, "글을 찾을 수 없습니다.")
    return post


# --- 게시판 관리 (관리자) ----------------------------------------------------


async def create_board(
    session: AsyncSession, actor: User, name: str, description: str | None
) -> Board:
    """게시판 생성. 이름 중복(활성)은 409. **인가는 라우터의 require_admin 이 맡는다.**"""
    clean = name.strip()
    if not clean:
        raise BoardServiceError(422, "게시판 이름을 입력해 주세요.")

    board = Board(name=clean, description=description, created_by=actor.id)
    session.add(board)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise BoardServiceError(409, "같은 이름의 게시판이 이미 있습니다.") from exc

    _record_audit(session, actor.id, "board.create", board.id, {"name": clean})
    await session.commit()
    await session.refresh(board)
    return board


async def update_board(
    session: AsyncSession,
    actor: User,
    board_id: int,
    *,
    name: str | None,
    description: str | None,
) -> Board:
    """게시판 이름/설명 수정. name/description 은 None 이면 그대로 둔다."""
    board = await session.get(Board, board_id)
    if board is None or not board.is_active:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")

    changed: dict[str, Any] = {}
    if name is not None:
        clean = name.strip()
        if not clean:
            raise BoardServiceError(422, "게시판 이름을 입력해 주세요.")
        if clean != board.name:
            changed["name"] = {"before": board.name, "after": clean}
            board.name = clean
    if description is not None and description != board.description:
        changed["description"] = {"before": board.description, "after": description}
        board.description = description

    if not changed:
        return board

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise BoardServiceError(409, "같은 이름의 게시판이 이미 있습니다.") from exc

    _record_audit(session, actor.id, "board.update", board.id, changed)
    await session.commit()
    await session.refresh(board)
    return board


async def delete_board(
    session: AsyncSession, storage: StorageService, actor: User, board_id: int
) -> int:
    """게시판 삭제(소프트) + 하위 글 전체의 첨부 오브젝트 회수. 반환: 회수한 첨부 수.

    글이 많을 수 있으므로 키 수집을 **한 쿼리**로 하고 `delete_many` 를 1000개씩 끊어 부른다.
    글·댓글 행은 남는다(소프트 삭제) — 사라지는 것은 오브젝트뿐이다.
    """
    board = await session.get(Board, board_id)
    if board is None or not board.is_active:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")

    # 1) 지울 키를 한 쿼리로 모은다 (글별 N+1 없이).
    keys = list(
        (
            await session.execute(
                select(BoardAttachment.object_key)
                .join(BoardPost, BoardPost.id == BoardAttachment.post_id)
                .where(BoardPost.board_id == board_id)
            )
        )
        .scalars()
        .all()
    )

    # 2) DB 확정 — 첨부 행은 하드 삭제, 글은 소프트 삭제, 게시판은 비활성.
    now = _now()
    await session.execute(
        delete(BoardAttachment).where(
            BoardAttachment.post_id.in_(
                select(BoardPost.id).where(BoardPost.board_id == board_id)
            )
        )
    )
    await session.execute(
        update(BoardPost)
        .where(BoardPost.board_id == board_id, BoardPost.is_deleted.is_(False))
        .values(is_deleted=True, deleted_at=now)
    )
    board.is_active = False
    _record_audit(
        session,
        actor.id,
        "board.delete",
        board_id,
        {"name": board.name, "attachments": len(keys)},
    )
    await session.commit()

    # 3) 오브젝트 회수 — best-effort. 실패해도 DB 는 이미 일관하다.
    await _purge_objects(storage, keys)
    return len(keys)


async def _purge_objects(storage: StorageService, keys: list[str]) -> None:
    """오브젝트 회수 — best-effort. 실패는 로그만 남기고 삼킨다.

    `purge_tree`(services/files.py) 4단계와 같은 선택이다. 여기서 예외를 올리면 이미 커밋된
    DB 와 어긋난 채로 5xx 가 나가고, 재시도해도 키를 알던 행이 없어 회수할 수 없다.
    실패 시 고아 오브젝트가 남는 것은 DB 를 진실 소스로 두는 대가다(spec 「남는 구멍 하나」).
    """
    for start in range(0, len(keys), _DELETE_CHUNK):
        chunk = keys[start : start + _DELETE_CHUNK]
        if not chunk:
            continue
        try:
            failed = await storage.delete_many_async(chunk)
            if failed:
                _log.warning("board_attachment_purge_partial", failed=len(failed))
        except Exception:  # noqa: BLE001 - best-effort 정리
            _log.warning("board_attachment_purge_failed", count=len(chunk))


async def board_counts(session: AsyncSession, board_id: int) -> tuple[int, int]:
    """게시판 하나의 (할당 그룹 수, 살아 있는 글 수).

    단건 응답이 0 을 채워 넣지 않게 하려고 둔다 — 목록에서는 맞고 상세에서는 0 인 필드가
    있으면 그 값을 믿을 수 없게 되고, 결국 아무도 안 쓰는 필드가 된다.
    """
    groups = int(
        (
            await session.execute(
                select(func.count())
                .select_from(BoardGroup)
                .where(BoardGroup.board_id == board_id)
            )
        ).scalar_one()
    )
    posts = int(
        (
            await session.execute(
                select(func.count())
                .select_from(BoardPost)
                .where(BoardPost.board_id == board_id, BoardPost.is_deleted.is_(False))
            )
        ).scalar_one()
    )
    return groups, posts


async def list_all_boards(
    session: AsyncSession,
) -> list[tuple[Board, int, int]]:
    """관리자용 전체 게시판 목록 — (게시판, 할당 그룹 수, 글 수). 활성만."""
    group_counts: dict[int, int] = {
        board_id: int(n)
        for board_id, n in (
            await session.execute(
                select(BoardGroup.board_id, func.count()).group_by(BoardGroup.board_id)
            )
        ).all()
    }
    post_counts: dict[int, int] = {
        board_id: int(n)
        for board_id, n in (
            await session.execute(
                select(BoardPost.board_id, func.count())
                .where(BoardPost.is_deleted.is_(False))
                .group_by(BoardPost.board_id)
            )
        ).all()
    }
    boards = (
        (
            await session.execute(
                select(Board).where(Board.is_active.is_(True)).order_by(Board.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        (b, group_counts.get(b.id, 0), post_counts.get(b.id, 0)) for b in boards
    ]


# --- 그룹 할당 (관리자) ------------------------------------------------------


async def list_board_groups(
    session: AsyncSession, board_id: int
) -> list[tuple[BoardGroup, str]]:
    """게시판에 할당된 (할당행, 그룹명) 목록."""
    board = await session.get(Board, board_id)
    if board is None or not board.is_active:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")
    rows = (
        await session.execute(
            select(BoardGroup, Group.name)
            .join(Group, Group.id == BoardGroup.group_id)
            .where(BoardGroup.board_id == board_id)
            .order_by(Group.name)
        )
    ).all()
    return [(bg, name) for bg, name in rows]


async def grant_board_group(
    session: AsyncSession,
    actor: User,
    board_id: int,
    group_id: int,
    permission: BoardPermission,
) -> BoardGroup:
    """그룹 할당(멱등 upsert — 이미 있으면 권한을 갈아끼운다)."""
    board = await session.get(Board, board_id)
    if board is None or not board.is_active:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")
    group = await session.get(Group, group_id)
    if group is None or not group.is_active:
        raise BoardServiceError(404, "그룹을 찾을 수 없습니다.")

    existing = (
        await session.execute(
            select(BoardGroup).where(
                BoardGroup.board_id == board_id, BoardGroup.group_id == group_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.permission = permission
        existing.granted_by = actor.id
        row = existing
    else:
        row = BoardGroup(
            board_id=board_id,
            group_id=group_id,
            permission=permission,
            granted_by=actor.id,
        )
        session.add(row)

    _record_audit(
        session,
        actor.id,
        "board.group_grant",
        board_id,
        {"group_id": group_id, "group_name": group.name, "permission": str(permission)},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def revoke_board_group(
    session: AsyncSession, actor: User, board_id: int, group_id: int
) -> None:
    """그룹 할당 회수. 글은 남고 접근만 끊긴다 — 작성자 본인도 예외가 아니다."""
    board = await session.get(Board, board_id)
    if board is None or not board.is_active:
        raise BoardServiceError(404, "게시판을 찾을 수 없습니다.")

    result = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(BoardGroup).where(
                BoardGroup.board_id == board_id, BoardGroup.group_id == group_id
            )
        ),
    )
    if not result.rowcount:
        raise BoardServiceError(404, "할당된 그룹이 아닙니다.")

    _record_audit(
        session, actor.id, "board.group_revoke", board_id, {"group_id": group_id}
    )
    await session.commit()


# --- 게시판 목록 (사용자) ----------------------------------------------------


@dataclass(frozen=True)
class BoardSummary:
    """내가 접근 가능한 게시판 한 줄. `permission` 이 None 이면 관리자 열람이다."""

    board: Board
    permission: BoardPermission | None
    post_count: int


async def list_accessible_boards(
    session: AsyncSession, user: User
) -> list[BoardSummary]:
    """**내가 접근 가능한 게시판만** 돌려준다. 이름조차 나오지 않는 것이 규약이다.

    관리자는 전부 본다(읽기 권한이 있으므로). 다만 `permission` 은 그룹 할당에서 온 값
    그대로여서, 관리자가 쓸 수 있는 게시판과 읽기만 되는 게시판이 응답에서 구분된다.
    """
    group_ids = await get_user_group_ids(session, user.id)
    admin = _is_admin(user)

    # 내 그룹이 붙은 게시판의 권한을 한 번에 모은다 — 게시판당 조회가 아니다.
    my_levels: dict[int, BoardPermission] = {}
    if group_ids:
        rows = (
            await session.execute(
                select(BoardGroup.board_id, BoardGroup.permission).where(
                    BoardGroup.group_id.in_(group_ids)
                )
            )
        ).all()
        by_board: dict[int, list[str]] = {}
        for board_id, permission in rows:
            by_board.setdefault(board_id, []).append(permission)
        for board_id, levels in by_board.items():
            resolved = resolve_effective_permission(levels)
            if resolved is not None:
                my_levels[board_id] = resolved

    if not my_levels and not admin:
        return []

    stmt = select(Board).where(Board.is_active.is_(True))
    if not admin:
        stmt = stmt.where(Board.id.in_(my_levels.keys()))
    boards = (await session.execute(stmt.order_by(Board.name))).scalars().all()
    if not boards:
        return []

    counts: dict[int, int] = {
        board_id: int(n)
        for board_id, n in (
            await session.execute(
                select(BoardPost.board_id, func.count())
                .where(
                    BoardPost.board_id.in_([b.id for b in boards]),
                    BoardPost.is_deleted.is_(False),
                )
                .group_by(BoardPost.board_id)
            )
        ).all()
    }
    return [
        BoardSummary(
            board=b,
            permission=my_levels.get(b.id),
            post_count=counts.get(b.id, 0),
        )
        for b in boards
    ]


# --- 글 ----------------------------------------------------------------------


@dataclass(frozen=True)
class PostSummary:
    """목록 한 줄 — 글 + 작성자 표시 이름 + 댓글 수."""

    post: BoardPost
    author_name: str
    comment_count: int
    attachment_count: int


async def list_posts(
    session: AsyncSession, user: User, board_id: int, page: int, size: int
) -> tuple[list[PostSummary], int]:
    """글 목록 (최신순, 페이지네이션). read 필요."""
    await ensure_board_access(session, user, board_id, "read")

    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(BoardPost)
                .where(
                    BoardPost.board_id == board_id, BoardPost.is_deleted.is_(False)
                )
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            select(BoardPost, User.display_name)
            .join(User, User.id == BoardPost.author_id)
            .where(BoardPost.board_id == board_id, BoardPost.is_deleted.is_(False))
            .order_by(BoardPost.created_at.desc(), BoardPost.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    posts = [p for p, _ in rows]
    if not posts:
        return [], total

    post_ids = [p.id for p in posts]
    # 댓글 수는 **삭제된 댓글도 센다** — 자리가 "삭제된 댓글입니다"로 남아 실제로 보이기 때문이다.
    comment_counts: dict[int, int] = {
        post_id: int(n)
        for post_id, n in (
            await session.execute(
                select(BoardComment.post_id, func.count())
                .where(BoardComment.post_id.in_(post_ids))
                .group_by(BoardComment.post_id)
            )
        ).all()
    }
    attachment_counts: dict[int, int] = {
        post_id: int(n)
        for post_id, n in (
            await session.execute(
                select(BoardAttachment.post_id, func.count())
                .where(BoardAttachment.post_id.in_(post_ids))
                .group_by(BoardAttachment.post_id)
            )
        ).all()
    }
    return (
        [
            PostSummary(
                post=post,
                author_name=name,
                comment_count=comment_counts.get(post.id, 0),
                attachment_count=attachment_counts.get(post.id, 0),
            )
            for post, name in rows
        ],
        total,
    )


@dataclass(frozen=True)
class PostDetail:
    post: BoardPost
    author_name: str
    attachments: list[BoardAttachment]
    can_edit: bool
    can_delete: bool


async def get_post(
    session: AsyncSession, user: User, board_id: int, post_id: int
) -> PostDetail:
    """글 상세 (첨부 목록 포함). read 필요."""
    access = await ensure_board_access(session, user, board_id, "read")
    post = await _get_post(session, board_id, post_id)

    author_name = (
        await session.execute(select(User.display_name).where(User.id == post.author_id))
    ).scalar_one_or_none() or "(알 수 없음)"
    attachments = list(
        (
            await session.execute(
                select(BoardAttachment)
                .where(BoardAttachment.post_id == post_id)
                .order_by(BoardAttachment.id)
            )
        )
        .scalars()
        .all()
    )
    is_author = post.author_id == user.id
    return PostDetail(
        post=post,
        author_name=author_name,
        attachments=attachments,
        # 수정은 작성자 본인만 — 관리자는 지울 수는 있어도 남의 글을 고치지 못한다.
        can_edit=is_author,
        can_delete=is_author or access.is_admin,
    )


async def create_post(
    session: AsyncSession, user: User, board_id: int, title: str, body: str
) -> BoardPost:
    """글 작성. write 필요."""
    await ensure_board_access(session, user, board_id, "write")
    clean = title.strip()
    if not clean:
        raise BoardServiceError(422, "제목을 입력해 주세요.")

    post = BoardPost(board_id=board_id, author_id=user.id, title=clean, body=body)
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post


async def update_post(
    session: AsyncSession,
    user: User,
    board_id: int,
    post_id: int,
    *,
    title: str | None,
    body: str | None,
) -> BoardPost:
    """글 수정 — **작성자 본인만**. 관리자도 남의 글은 고치지 못한다.

    관문은 read 다. spec 의 API 표에서 이 줄의 권한 칸("작성자")은 read 게이트 **위에 얹는
    행위자 조건**을 뜻한다 — 같은 표의 DELETE 가 "작성자·관리자"인데 관리자 삭제에 write 가
    필요 없는 것과 같은 읽기다. 접근을 잃은 게시판이면 작성자 검사에 닿기 전에 404 가 난다.
    """
    await ensure_board_access(session, user, board_id, "read")
    post = await _get_post(session, board_id, post_id)
    if post.author_id != user.id:
        raise BoardServiceError(403, "본인이 쓴 글만 수정할 수 있습니다.")

    if title is not None:
        clean = title.strip()
        if not clean:
            raise BoardServiceError(422, "제목을 입력해 주세요.")
        post.title = clean
    if body is not None:
        post.body = body

    await session.commit()
    await session.refresh(post)
    return post


async def delete_post(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    board_id: int,
    post_id: int,
) -> None:
    """글 삭제 — 작성자 본인 또는 시스템 관리자. 첨부는 **그 자리에서** 회수한다.

    순서는 `purge_tree`(services/files.py)의 규약 그대로다:
      1) 지울 object_key 를 모은다.
      2) DB 확정 — 첨부 행 하드 삭제, 글은 is_deleted=TRUE. 커밋.
      3) delete_many_async — best-effort. 실패해도 DB 는 이미 일관하다.

    사이드카도 유예도 없다. 드라이브가 첨부를 7일 붙드는 이유는 휴지통 복원인데, 게시판 글에는
    복원 경로가 없다. 되돌릴 사용량(개인 할당량)도 없어 두 번 지워도 두 번째가 무해하므로
    purger 의 Redis 리스 같은 장치도 필요 없다.
    """
    access = await ensure_board_access(session, user, board_id, "read")
    post = await _get_post(session, board_id, post_id)
    if post.author_id != user.id and not access.is_admin:
        raise BoardServiceError(403, "글을 삭제할 권한이 없습니다.")

    keys = list(
        (
            await session.execute(
                select(BoardAttachment.object_key).where(
                    BoardAttachment.post_id == post_id
                )
            )
        )
        .scalars()
        .all()
    )

    await session.execute(
        delete(BoardAttachment).where(BoardAttachment.post_id == post_id)
    )
    post.is_deleted = True
    post.deleted_at = _now()
    if access.is_admin and post.author_id != user.id:
        _record_audit(
            session,
            user.id,
            "board.post_delete",
            board_id,
            {"post_id": post_id, "author_id": post.author_id, "title": post.title},
        )
    await session.commit()

    await _purge_objects(storage, keys)


# --- 댓글 --------------------------------------------------------------------


@dataclass(frozen=True)
class CommentView:
    comment: BoardComment
    author_name: str
    can_delete: bool


async def list_comments(
    session: AsyncSession, user: User, board_id: int, post_id: int
) -> list[CommentView]:
    """댓글 목록 (오래된 순, 평평한 목록 하나). read 필요.

    삭제된 댓글도 **행은 돌려준다** — 라우터가 본문을 "삭제된 댓글입니다"로 갈아 끼운다.
    자리를 남기는 이유는 아래 답글이 대화 맥락을 잃지 않게 하기 위해서다.
    """
    access = await ensure_board_access(session, user, board_id, "read")
    await _get_post(session, board_id, post_id)

    rows = (
        await session.execute(
            select(BoardComment, User.display_name)
            .join(User, User.id == BoardComment.author_id)
            .where(BoardComment.post_id == post_id)
            .order_by(BoardComment.created_at, BoardComment.id)
        )
    ).all()
    return [
        CommentView(
            comment=c,
            author_name=name,
            can_delete=not c.is_deleted
            and (c.author_id == user.id or access.is_admin),
        )
        for c, name in rows
    ]


async def create_comment(
    session: AsyncSession, user: User, board_id: int, post_id: int, body: str
) -> BoardComment:
    """댓글 작성. write 필요 — read 만 있으면 읽기만 된다."""
    await ensure_board_access(session, user, board_id, "write")
    await _get_post(session, board_id, post_id)

    clean = body.strip()
    if not clean:
        raise BoardServiceError(422, "댓글 내용을 입력해 주세요.")

    comment = BoardComment(post_id=post_id, author_id=user.id, body=clean)
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return comment


async def delete_comment(
    session: AsyncSession, user: User, board_id: int, post_id: int, comment_id: int
) -> None:
    """댓글 삭제 — 작성자 본인 또는 시스템 관리자. 소프트 삭제로 자리는 남는다."""
    access = await ensure_board_access(session, user, board_id, "read")
    await _get_post(session, board_id, post_id)

    comment = await session.get(BoardComment, comment_id)
    if comment is None or comment.post_id != post_id or comment.is_deleted:
        raise BoardServiceError(404, "댓글을 찾을 수 없습니다.")
    if comment.author_id != user.id and not access.is_admin:
        raise BoardServiceError(403, "댓글을 삭제할 권한이 없습니다.")

    comment.is_deleted = True
    comment.deleted_at = _now()
    if access.is_admin and comment.author_id != user.id:
        _record_audit(
            session,
            user.id,
            "board.comment_delete",
            board_id,
            {"post_id": post_id, "comment_id": comment_id, "author_id": comment.author_id},
        )
    await session.commit()


# --- 첨부 --------------------------------------------------------------------


def _upload_size(upload: UploadFile) -> int:
    """UploadFile 의 바이트 크기 (전체 메모리 적재 없이). files.py 와 같은 방식."""
    if upload.size is not None:
        return upload.size
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(0)
    return size


async def add_attachment(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    board_id: int,
    post_id: int,
    upload: UploadFile,
) -> BoardAttachment:
    """첨부 업로드 (단일 요청 multipart). write + 작성자 본인.

    개인 할당량을 차감하지 않고 드라이브 사용량 통계에도 잡히지 않는다 — 대신 파일당
    10 MB / 글당 5개로 작게 묶는다. 큰 파일은 드라이브에 올리고 공유 링크를 본문에 붙인다.
    """
    await ensure_board_access(session, user, board_id, "write")
    post = await _get_post(session, board_id, post_id)
    if post.author_id != user.id:
        raise BoardServiceError(403, "본인이 쓴 글에만 첨부할 수 있습니다.")

    size = _upload_size(upload)
    limit = settings.board_attachment_max_bytes
    if size > limit:
        raise BoardServiceError(
            413, f"첨부 파일은 {limit // (1024 * 1024)}MB 를 넘을 수 없습니다."
        )

    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(BoardAttachment)
                .where(BoardAttachment.post_id == post_id)
            )
        ).scalar_one()
    )
    if count >= settings.board_attachment_max_count:
        raise BoardServiceError(
            409, f"첨부는 글당 {settings.board_attachment_max_count}개까지입니다."
        )

    filename = (upload.filename or "untitled").strip() or "untitled"
    mime = upload.content_type or "application/octet-stream"
    key = build_attachment_key(board_id, post_id)

    # 오브젝트를 먼저 올리고 행을 만든다. 반대 순서면 put 실패 시 오브젝트 없는 행이 남고,
    # 이 순서면 행 생성 실패 시 고아 오브젝트가 남는데 후자를 아래에서 정리할 수 있다.
    await upload.seek(0)
    await storage.put_async(key, upload.file, size, mime)

    attachment = BoardAttachment(
        post_id=post_id,
        object_key=key,
        filename=filename,
        mime_type=mime,
        size=size,
    )
    session.add(attachment)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await _purge_objects(storage, [key])
        raise

    await session.refresh(attachment)
    return attachment


async def delete_attachment(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    board_id: int,
    post_id: int,
    attachment_id: int,
) -> None:
    """첨부 개별 제거 — 작성자 본인 또는 관리자. 글 삭제와 같은 회수 경로를 탄다."""
    access = await ensure_board_access(session, user, board_id, "read")
    post = await _get_post(session, board_id, post_id)
    if post.author_id != user.id and not access.is_admin:
        raise BoardServiceError(403, "첨부를 삭제할 권한이 없습니다.")

    attachment = await session.get(BoardAttachment, attachment_id)
    if attachment is None or attachment.post_id != post_id:
        raise BoardServiceError(404, "첨부를 찾을 수 없습니다.")

    key = attachment.object_key
    await session.delete(attachment)
    await session.commit()

    await _purge_objects(storage, [key])


async def prepare_attachment_download(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    board_id: int,
    post_id: int,
    attachment_id: int,
) -> tuple[str, str, str]:
    """게이트웨이 스트리밍 다운로드 준비. read 필요.

    반환: (internal_redirect_path, filename, mime). 드라이브와 같은 X-Accel-Redirect 모델이라
    presigned URL 이 브라우저에 노출되지 않는다. 미리보기·썸네일은 1차 범위 밖이다.
    """
    await ensure_board_access(session, user, board_id, "read")
    await _get_post(session, board_id, post_id)

    attachment = await session.get(BoardAttachment, attachment_id)
    if attachment is None or attachment.post_id != post_id:
        raise BoardServiceError(404, "첨부를 찾을 수 없습니다.")

    presigned = await storage.presign_get_async(attachment.object_key)
    internal = storage.to_internal_redirect(presigned)
    return internal, attachment.filename, attachment.mime_type or "application/octet-stream"
