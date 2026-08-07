"""routines.day_of_month 추가 — 매월 특정일 루틴

Revision ID: 0014_routine_day_of_month
Revises: 0013_groups_name_unique_active
Create Date: 2026-07-22

'매월 특정일'(frequency='monthly') 주기를 위해 날짜 하나를 담는 컬럼을 더한다. 요일처럼
여럿을 고르는 게 아니라 하루만 지정하므로 콤마 문자열이 아니라 정수 한 칸이다.

monthly 가 아닌 루틴에서는 NULL 이라 기존 행은 손댈 것이 없다. 값 범위(1~31) 검증은
스키마/서비스 계층에서 한다 — frequency 가 VARCHAR CHECK 없이 운영돼 온 것과 같은 결이다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_routine_day_of_month"
down_revision: str | None = "0013_groups_name_unique_active"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("routines", sa.Column("day_of_month", sa.Integer(), nullable=True))


def downgrade() -> None:
    # monthly 루틴은 주기를 잃으면 '매일'로 잘못 읽히므로 비활성으로 내려두고 컬럼을 지운다.
    op.execute("UPDATE routines SET is_active = FALSE WHERE frequency = 'monthly'")
    op.drop_column("routines", "day_of_month")
