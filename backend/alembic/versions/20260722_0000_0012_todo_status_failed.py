"""todo_items.status: 'skipped' → 'failed'

Revision ID: 0012_todo_status_failed
Revises: 0011_todos_routines
Create Date: 2026-07-22

X 표시의 의미를 '오늘은 안 함(skip)'에서 '수행 실패(자기 반성으로 남기는 명시적 미달성)'로
바꾼다. 의미가 뒤집혔으므로 저장값도 함께 옮겨 화면·코드·DB 용어를 일치시킨다.

집계 규칙도 함께 바뀐다(서비스 계층): skipped 는 달성률 분모에서 제외됐지만, failed 는
미달성으로 분모에 포함된다. 즉 이 마이그레이션 이후 과거 기록의 달성률이 낮아질 수 있다 —
같은 데이터를 '빼고 세던 것'에서 '넣고 세는 것'으로 해석을 바꾼 결과이며 의도된 변화다.

status 는 CHECK 제약 없는 VARCHAR(20) 이라 데이터 UPDATE 만으로 끝난다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_todo_status_failed"
down_revision: str | None = "0011_todos_routines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE todo_items SET status = 'failed' WHERE status = 'skipped'")


def downgrade() -> None:
    op.execute("UPDATE todo_items SET status = 'skipped' WHERE status = 'failed'")
