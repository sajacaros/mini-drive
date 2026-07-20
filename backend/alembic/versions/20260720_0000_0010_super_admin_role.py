"""최초 셋업 관리자를 super_admin 으로 승격 (전역 역할 확장)

Revision ID: 0010_super_admin_role
Revises: 0009_favorites_recents
Create Date: 2026-07-20

전역 역할에 super_admin 을 도입한다(users.role 은 VARCHAR 라 스키마 변경 없음 — 값만 추가).
super_admin 은 다른 사용자에게 admin 권한을 부여/회수할 수 있는 최상위 계정이다.

데이터 이관: 기존 인스턴스는 셋업 최초 계정이 admin 으로 저장돼 있으므로, 가장 먼저 생성된
admin(최소 id)을 super_admin 으로 승격한다. 이미 super_admin 이 있으면 아무것도 하지 않는다
(멱등). 셋업이 애초에 super_admin 을 만드는 신규 인스턴스는 이 이관이 no-op 이다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_super_admin_role"
down_revision: str | None = "0009_favorites_recents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 가장 먼저 생성된 admin 1명을 super_admin 으로 승격. super_admin 이 이미 있으면 no-op.
    op.execute(
        sa.text(
            """
            UPDATE users SET role = 'super_admin'
            WHERE id = (
                SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1
            )
            AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'super_admin')
            """
        )
    )


def downgrade() -> None:
    # super_admin 을 일반 admin 으로 되돌린다.
    op.execute(sa.text("UPDATE users SET role = 'admin' WHERE role = 'super_admin'"))
