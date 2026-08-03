"""todo_items.start_time / routines.start_time 추가 — 시작 시각 정렬

Revision ID: 0019_todo_start_time
Revises: 0018_chat_sessions
Create Date: 2026-08-03

할 일에 '시작 시각'을 10분 단위로 붙이고 그 순서대로 보이게 한다. 컬럼 하나로 종일 여부와
시각을 함께 표현한다 — NULL 이 곧 '종일'이다. is_all_day 불린을 따로 두면
(start_time=NULL, is_all_day=false) 같은 모순 상태가 생기므로 두지 않는다.

정렬은 start_time NULLS FIRST 이므로 값이 NULL 인 기존 행(= 종일)은 지금처럼 위에 모이고,
그 안에서는 여전히 sort_order(드래그 순서)를 따른다. 기존 데이터는 손댈 것이 없다.

routines.start_time 은 지금 항상 NULL(루틴 = 종일)이지만, 물질화가 이 값을 그대로 복사하므로
나중에 '07:00 운동' 같은 시각 있는 루틴을 열 때 마이그레이션 없이 요청 스키마만 더하면 된다.

10분 단위(minute % 10 == 0, second == 0) 검증은 스키마 계층에서 한다 — frequency 가
VARCHAR CHECK 없이 운영돼 온 것과 같은 결이다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_todo_start_time"
down_revision: str | None = "0018_chat_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("todo_items", sa.Column("start_time", sa.Time(), nullable=True))
    op.add_column("routines", sa.Column("start_time", sa.Time(), nullable=True))
    # 하루치 조회 정렬(start_time NULLS FIRST → sort_order → id)을 그대로 받는 인덱스.
    op.create_index(
        "idx_todo_items_user_date_time",
        "todo_items",
        ["user_id", "todo_date", "start_time", "sort_order"],
    )


def downgrade() -> None:
    # 시각을 잃으면 전부 종일로 되돌아간다 — 드래그 순서(sort_order)는 그대로 남는다.
    op.drop_index("idx_todo_items_user_date_time", table_name="todo_items")
    op.drop_column("routines", "start_time")
    op.drop_column("todo_items", "start_time")
