"""게시판 테이블 5종 (spec/group-board.md).

드라이브(`files` + `file_group_permissions`)와 **축을 나눈다**. 게시판은 트리가 아니라 평평하고,
상속이 없으므로 판정이 `내 그룹 ∩ 이 게시판에 할당된 그룹` 조인 한 번이다 — recursive CTE 도
세대 카운터 캐시도 여기에는 없다. 자세한 근거는 spec 「왜 드라이브 권한축을 쓰지 않는가」.

삭제는 전부 소프트 삭제다. 다만 **첨부 행만은 하드 삭제**한다 — 글에 복원 경로가 없어
오브젝트를 붙들 이유가 없고, 행이 남으면 이미 사라진 오브젝트를 가리키게 된다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import BoardPermission


class Board(Base):
    """게시판 하나. 시스템 관리자만 만들고 고치고 지운다 (게시판별 운영자 역할 없음)."""

    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # 이름 점유를 활성 게시판으로 한정한다 — `groups` 가 uq_groups_name_active 로 푼 것과
    # 같은 문제(소프트 삭제된 행이 이름을 영원히 잡는다)이고 같은 해법이다.
    __table_args__ = (
        Index(
            "uq_boards_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<Board id={self.id} name={self.name!r}>"


class BoardGroup(Base):
    """게시판↔그룹 할당 (N:M). 할당마다 read/write 가 갈린다.

    회수는 행 삭제(하드)다. 소프트 삭제로 남길 이력이 없고, 누가 언제 붙였다 뗐는지는
    audit_logs(`board.group_grant` / `board.group_revoke`)가 이미 들고 있다.
    """

    __tablename__ = "board_groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    board_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    # 'read' | 'write' (models.enums.BoardPermission). native enum 미사용(VARCHAR).
    permission: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{BoardPermission.READ}'")
    )
    granted_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("board_id", "group_id", name="uq_board_groups_board_group"),
        # 판정의 반대 방향 조회 — "내 그룹들이 붙은 게시판"(목록 화면)이 이 인덱스를 탄다.
        Index("idx_board_groups_group", "group_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<BoardGroup board_id={self.board_id} group_id={self.group_id} "
            f"permission={self.permission}>"
        )


class BoardPost(Base):
    """글 한 건. 본문은 마크다운이며 `components/Markdown.tsx` 가 그대로 렌더한다."""

    __tablename__ = "board_posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    board_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # 목록은 게시판별 최신순 한 가지뿐이다 — 삭제된 글은 어디에도 안 나오므로 부분 인덱스.
        Index(
            "idx_board_posts_board_created",
            "board_id",
            "created_at",
            postgresql_where=text("NOT is_deleted"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<BoardPost id={self.id} board_id={self.board_id} title={self.title!r}>"


class BoardComment(Base):
    """댓글. 대댓글 없이 평평한 목록 하나이고 수정도 없다(지우고 다시 쓴다).

    삭제는 소프트 삭제이며 자리는 "삭제된 댓글입니다"로 남긴다 — 아래 답글이 대화 맥락을
    잃지 않게. 그래서 목록 쿼리가 `is_deleted` 로 거르지 않고 본문만 가린다.
    """

    __tablename__ = "board_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("board_posts.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_board_comments_post", "post_id", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<BoardComment id={self.id} post_id={self.post_id}>"


class BoardAttachment(Base):
    """글 첨부. 드라이브 `files` 와 분리되어 개인 할당량을 차감하지 않는다.

    같은 MinIO 버킷을 쓰되 키만 `board/{board_id}/{post_id}/{uuid}` 로 가른다. 삭제는
    **하드**이고 오브젝트 회수를 같은 요청 안에서 끝낸다(spec 「회수는 삭제하는 그 자리에서」).
    """

    __tablename__ = "board_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("board_posts.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_board_attachments_post", "post_id"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<BoardAttachment id={self.id} post_id={self.post_id} name={self.filename!r}>"
