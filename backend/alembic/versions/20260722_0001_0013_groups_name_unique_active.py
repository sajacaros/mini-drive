"""groups.name UNIQUE → 활성 그룹에만 적용되는 부분 유니크 인덱스

Revision ID: 0013_groups_name_unique_active
Revises: 0012_todo_status_failed
Create Date: 2026-07-22

그룹 삭제는 소프트 삭제(is_active=FALSE)라 행이 남는데, UNIQUE(name) 이 삭제 여부를 가리지
않아 삭제된 그룹이 이름을 영원히 점유했다. 목록에는 보이지도 않는 그룹 때문에 같은 이름으로
다시 만들 수 없는 상태 — 사용자에게는 원인을 알 수 없는 409 로 보인다.

활성 그룹끼리만 이름이 겹치지 않으면 되므로 WHERE is_active 부분 인덱스로 바꾼다. 기존
UNIQUE 가 더 강한 제약이었으므로 현재 데이터는 그대로 새 인덱스를 만족한다.

다운그레이드는 비활성 그룹 간 이름 중복이 이미 생긴 뒤라면 실패할 수 있다(전역 UNIQUE 를
되돌리는 것이므로 의도된 동작).
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_groups_name_unique_active"
down_revision: str | None = "0012_todo_status_failed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_groups_name", "groups", type_="unique")
    op.create_index(
        "uq_groups_name_active",
        "groups",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_groups_name_active", table_name="groups")
    op.create_unique_constraint("uq_groups_name", "groups", ["name"])
