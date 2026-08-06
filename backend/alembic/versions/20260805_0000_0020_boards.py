"""그룹 게시판 — boards / board_groups / board_posts / board_comments / board_attachments

Revision ID: 0020_boards
Revises: 0019_todo_start_time
Create Date: 2026-08-05

관리자가 게시판을 만들고 **그룹을 할당해서** 연다(spec/group-board.md). 권한축을 드라이브와
나눈 것이 이 마이그레이션의 요지다 — `file_group_permissions` 를 재사용하지 않고
`board_groups` 를 새로 판다. 상속이 없으니 판정이 조인 한 번이고, 드라이브의 상속 규칙
(넓히기 전용)이 평평한 게시판에서 탈출구 없이 굳는 문제도 피한다.

인덱스 세 가지 결정:
  - `uq_boards_name_active` — 이름 점유를 활성 게시판으로만 한정한다(groups 와 같은 해법).
  - `idx_board_posts_board_created` 는 `NOT is_deleted` 부분 인덱스다. 삭제된 글은 목록에도
    상세에도 나오지 않아 인덱스에 담아 둘 이유가 없다.
  - `idx_board_groups_group` 은 "내 그룹들이 붙은 게시판" 방향(목록 화면)을 받는다. UNIQUE
    (board_id, group_id) 는 반대 방향만 커버한다.

board_attachments 에는 소프트 삭제 컬럼이 없다. 글을 지우면 첨부 행은 그 요청 안에서 하드
삭제되고 MinIO 오브젝트도 함께 회수된다 — 글에 복원 경로가 없어 붙들 이유가 없다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_boards"
down_revision: str | None = "0019_todo_start_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "boards",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_boards_name_active",
        "boards",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "board_groups",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "board_id",
            sa.BigInteger(),
            sa.ForeignKey("boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.BigInteger(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'read'"),
        ),
        sa.Column(
            "granted_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("board_id", "group_id", name="uq_board_groups_board_group"),
    )
    op.create_index("idx_board_groups_group", "board_groups", ["group_id"])

    op.create_table(
        "board_posts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "board_id",
            sa.BigInteger(),
            sa.ForeignKey("boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_board_posts_board_created",
        "board_posts",
        ["board_id", "created_at"],
        postgresql_where=sa.text("NOT is_deleted"),
    )

    op.create_table(
        "board_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.BigInteger(),
            sa.ForeignKey("board_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_board_comments_post", "board_comments", ["post_id", "created_at"])

    op.create_table(
        "board_attachments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.BigInteger(),
            sa.ForeignKey("board_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_board_attachments_post", "board_attachments", ["post_id"])


def downgrade() -> None:
    # 오브젝트(board/… 키)는 여기서 손대지 않는다 — 마이그레이션이 MinIO 를 지우면 되돌릴 수
    # 없는 삭제가 스키마 롤백에 딸려 온다. 고아 오브젝트는 남지만 되살릴 수는 있다.
    op.drop_index("idx_board_attachments_post", table_name="board_attachments")
    op.drop_table("board_attachments")
    op.drop_index("idx_board_comments_post", table_name="board_comments")
    op.drop_table("board_comments")
    op.drop_index("idx_board_posts_board_created", table_name="board_posts")
    op.drop_table("board_posts")
    op.drop_index("idx_board_groups_group", table_name="board_groups")
    op.drop_table("board_groups")
    op.drop_index("uq_boards_name_active", table_name="boards")
    op.drop_table("boards")
