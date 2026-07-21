"""routines + todo_items (데일리 투두 · 반복 루틴)

Revision ID: 0011_todos_routines
Revises: 0010_super_admin_role
Create Date: 2026-07-21

데일리 투두(todo_items)와 반복 루틴(routines) 테이블을 추가한다. 둘 다 users 삭제 시
CASCADE 로 정리되고, todo_items.routine_id 는 루틴 삭제 시 SET NULL(임시 항목으로 보존).

멱등성(0009 가드 패턴): 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 테이블을
out-of-band 재생성하므로, 이미 존재하는 객체를 다시 만들지 않도록 테이블/인덱스별로 가드한다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_todos_routines"
down_revision: str | None = "0010_super_admin_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    insp = sa.inspect(op.get_bind())
    return name in {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    # ── routines ───────────────────────────────────────────────────────
    if not _has_table("routines"):
        op.create_table(
            "routines",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column(
                "frequency",
                sa.String(length=20),
                server_default=sa.text("'daily'"),
                nullable=False,
            ),
            sa.Column("days_of_week", sa.String(length=20), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                server_default=sa.text("true"),
                nullable=False,
            ),
            sa.Column(
                "sort_order",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_index("routines", "idx_routines_user"):
        op.create_index("idx_routines_user", "routines", ["user_id"])

    # ── todo_items ─────────────────────────────────────────────────────
    if not _has_table("todo_items"):
        op.create_table(
            "todo_items",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("todo_date", sa.Date(), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                server_default=sa.text("'pending'"),
                nullable=False,
            ),
            sa.Column("routine_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "sort_order",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["routine_id"], ["routines.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    # 같은 루틴이 같은 날 중복 물질화되는 것 방지 (routine_id IS NOT NULL 부분 유니크).
    if not _has_index("todo_items", "uq_todo_items_routine_day"):
        op.create_index(
            "uq_todo_items_routine_day",
            "todo_items",
            ["user_id", "todo_date", "routine_id"],
            unique=True,
            postgresql_where=sa.text("routine_id IS NOT NULL"),
        )
    if not _has_index("todo_items", "idx_todo_items_user_date"):
        op.create_index(
            "idx_todo_items_user_date",
            "todo_items",
            ["user_id", "todo_date"],
        )


def downgrade() -> None:
    if _has_index("todo_items", "idx_todo_items_user_date"):
        op.drop_index("idx_todo_items_user_date", table_name="todo_items")
    if _has_index("todo_items", "uq_todo_items_routine_day"):
        op.drop_index("uq_todo_items_routine_day", table_name="todo_items")
    if _has_table("todo_items"):
        op.drop_table("todo_items")
    if _has_index("routines", "idx_routines_user"):
        op.drop_index("idx_routines_user", table_name="routines")
    if _has_table("routines"):
        op.drop_table("routines")
