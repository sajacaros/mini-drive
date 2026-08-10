"""todo_items.origin_date 추가 — 할 일 날짜 옮기기/복제

Revision ID: 0021_todo_origin_date
Revises: 0020_boards
Create Date: 2026-08-10

할 일을 다른 날짜로 옮길 수 있게 하면서, 루틴 파생 항목의 "그날 한 번만 만든다"는 규칙을
그대로 지키려면 날짜가 둘로 갈라져야 한다.

  - todo_date   : 지금 이 항목이 보이는 날짜. 옮기면 바뀐다. 하루치 조회·리포트 집계 기준.
  - origin_date : 이 항목이 원래 속했던 날짜. 옮겨도 바뀌지 않는다. 루틴 물질화 판정 기준.

물질화(ensure_day_materialized)는 "그날 그 루틴의 항목이 이미 있는가"를 origin_date 로 묻는다.
그래서 오늘치 루틴 항목을 내일로 미뤄도 오늘 자리는 그 항목이 계속 점유하고 있어 다시 생기지
않고, 내일에는 옮겨온 오늘치(origin=오늘)와 내일치(origin=내일)가 서로 다른 행으로 공존한다.
유니크 인덱스도 같은 이유로 (user_id, origin_date, routine_id) 로 옮긴다 — todo_date 로 두면
그 둘이 같은 날에 모이는 순간 충돌한다.

기존 행은 옮긴 적이 없으므로 origin_date = todo_date 로 백필한다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_todo_origin_date"
down_revision: str | None = "0020_boards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL 허용으로 붙이고 백필한 뒤 NOT NULL 로 조인다 — 기존 행이 있는 테이블에 NOT NULL
    # 컬럼을 한 번에 추가할 수 없기 때문이다.
    op.add_column("todo_items", sa.Column("origin_date", sa.Date(), nullable=True))
    op.execute("UPDATE todo_items SET origin_date = todo_date WHERE origin_date IS NULL")
    op.alter_column("todo_items", "origin_date", nullable=False)

    # 루틴 중복 물질화 방지 기준을 todo_date → origin_date 로 옮긴다(임시 항목은 여전히 제외).
    op.drop_index("uq_todo_items_routine_day", table_name="todo_items")
    op.create_index(
        "uq_todo_items_routine_day",
        "todo_items",
        ["user_id", "origin_date", "routine_id"],
        unique=True,
        postgresql_where=sa.text("routine_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 옮긴 항목이 있는 상태로 되돌리면 (user_id, todo_date, routine_id) 가 겹칠 수 있다.
    # 유니크 인덱스를 다시 만들기 전에 옮긴 루틴 항목을 원래 날짜로 되돌려 충돌을 없앤다
    # (옮긴 이력을 잃는 대신 인덱스 생성이 실패하지 않는다).
    op.execute(
        "UPDATE todo_items SET todo_date = origin_date "
        "WHERE routine_id IS NOT NULL AND todo_date <> origin_date"
    )
    op.drop_index("uq_todo_items_routine_day", table_name="todo_items")
    op.create_index(
        "uq_todo_items_routine_day",
        "todo_items",
        ["user_id", "todo_date", "routine_id"],
        unique=True,
        postgresql_where=sa.text("routine_id IS NOT NULL"),
    )
    op.drop_column("todo_items", "origin_date")
