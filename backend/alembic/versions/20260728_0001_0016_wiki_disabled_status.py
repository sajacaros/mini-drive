"""꺼진 위키 문서를 질의 대상에서 제외 — status='disabled' 로 정리 (spec/wiki-index.md)

Revision ID: 0016_wiki_disabled_status
Revises: 0015_wiki_index
Create Date: 2026-07-28

끄기의 계약은 "차단은 즉시, 삭제는 유예"다. 그런데 끄기가 큐만 비우고 `wiki_documents.status`
를 그대로 뒀던 탓에, 이미 색인된 트리가 유예 기간(기본 30일) 내내 질의에 계속 잡혔다.
소유자는 껐다고 생각하는데 그 문서로 답이 나가는 상태였다.

코드는 이제 끄는 시점에 `status='disabled'` 로 내린다(검색은 ready/stale 만 본다). 이 리비전은
**그 수정 이전에 꺼진 행**을 같은 상태로 맞춘다 — 그렇지 않으면 기존 배포에서 이미 꺼진
문서들이 계속 검색된다.

대상은 "유효 위키가 꺼져 있는데 질의 가능 상태로 남은 행"이다. 유효 여부는 조상 경로에서 가장
가까운 wiki_enabled 명시값으로 판정한다(resolve_wiki_state 와 같은 규칙).

트리는 지우지 않는다 — 유예의 목적이 재켜기 비용 절약이고, 다시 켜면 재색인 없이 ready 로
복구된다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_wiki_disabled_status"
down_revision: str | None = "0015_wiki_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("wiki_documents"):
        return
    op.execute(
        sa.text(
            """
            WITH RECURSIVE chain AS (
                SELECT w.file_id AS target, f.id AS node, f.parent_folder_id,
                       f.wiki_enabled, 0 AS depth
                FROM wiki_documents w
                JOIN files f ON f.id = w.file_id
                UNION ALL
                SELECT c.target, p.id, p.parent_folder_id, p.wiki_enabled, c.depth + 1
                FROM chain c JOIN files p ON p.id = c.parent_folder_id
            ),
            nearest AS (
                SELECT DISTINCT ON (target) target, wiki_enabled
                FROM chain
                WHERE wiki_enabled IS NOT NULL
                ORDER BY target, depth ASC
            )
            UPDATE wiki_documents w
            SET status = 'disabled'
            WHERE w.status IN ('ready', 'stale')
              AND COALESCE(
                    (SELECT n.wiki_enabled FROM nearest n WHERE n.target = w.file_id),
                    FALSE
                  ) IS FALSE
            """
        )
    )


def downgrade() -> None:
    # 'disabled' 는 이 리비전이 도입한 값이므로, 되돌릴 때는 트리가 있는 것만 ready 로 복원한다.
    if not _has_table("wiki_documents"):
        return
    op.execute(
        sa.text(
            "UPDATE wiki_documents SET status = 'ready' "
            "WHERE status = 'disabled' AND tree IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE wiki_documents SET status = 'pending' "
            "WHERE status = 'disabled' AND tree IS NULL"
        )
    )
