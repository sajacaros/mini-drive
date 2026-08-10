"""wiki_documents 에 attempts/last_attempt_at 추가 — 정합 스윕의 재시도 백오프

Revision ID: 0022_wiki_reconcile
Revises: 0021_todo_origin_date
Create Date: 2026-08-10

정합 스윕(`wiki_indexer.reconcile`)이 `failed` 로 남은 문서를 다시 큐에 넣는데, 그냥 넣으면
영구 실패가 확정된 문서를 매일 다시 굽는다 — 상한을 넘긴 파일이나 깨진 인코딩은 몇 번을 돌려도
같은 곳에서 죽는다. 그 낭비가 그대로 GPU 점유라(spec/wiki-index.md 「GPU 경합」) 시도 횟수와
마지막 시도 시각을 남겨 백오프를 건다.

  - attempts        : 연속 실패 횟수. 성공하면 0 으로 되돌린다.
  - last_attempt_at : 마지막 시도 시각. 스윕을 하루보다 자주 돌려도 재시도가 몰리지 않게 한다.

기존 행은 시도 이력이 없으므로 attempts=0 으로 백필한다. `failed` 로 남아 있던 행도 0 에서
시작해 스윕이 한 번은 다시 시도한다 — 이 리비전 이전에는 재시도 경로 자체가 없어 소유자가
토글을 껐다 켜지 않는 한 영영 실패로 남아 있었기 때문이다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_wiki_reconcile"
down_revision: str | None = "0021_todo_origin_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default 로 붙여 기존 행을 한 번에 백필하고, 이후 삽입은 애플리케이션 기본값이
    # 채운다(0021 과 달리 NOT NULL 을 나중에 조일 필요가 없다).
    op.add_column(
        "wiki_documents",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "wiki_documents",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wiki_documents", "last_attempt_at")
    op.drop_column("wiki_documents", "attempts")
