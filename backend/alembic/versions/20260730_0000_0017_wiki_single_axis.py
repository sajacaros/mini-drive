"""위키를 단일 축으로 — 켜진 문서에 `@전사 read` 를 채운다 (spec/wiki-index.md)

Revision ID: 0017_wiki_single_axis
Revises: 0016_wiki_disabled_status
Create Date: 2026-07-30

인덱싱과 전사 공개가 독립 스위치였다. 실제 데이터에서 색인된 467건 중 `@전사 read` 가 붙은
것은 **2건**이었다 — 사람들은 인덱싱만 켜고 공개는 켜지 않았고, 그러면 남이 질의해도 아무것도
찾지 못한다. 켰는데 동작하지 않는 상태라 축을 하나로 합쳤다(spec 「왜 스위치가 하나인가」).

코드는 이제 켜는 시점에 두 가지를 함께 한다. 이 리비전은 **그 수정 이전에 켜 둔 문서**를 같은
상태로 맞춘다. 안 맞추면 기존 배포의 위키는 소유자 본인 외에는 아무도 못 쓰는 채로 남는다.

대상은 "유효 위키가 켜져 있고 질의 가능한 트리가 있는 파일"이다. 유효 여부는 조상 경로에서
가장 가까운 `wiki_enabled` 명시값으로 판정한다(`resolve_wiki_state` 와 같은 규칙).

**부여는 파일에만, 상속 없이 건다.** 폴더에 `inherit_to_children=TRUE` 로 주면 인덱싱 대상이
아닌 PDF·이미지까지 열리고, 소유자가 파일에서 위키를 꺼도 공개가 상속으로 남아 불변식이
그 파일에서 깨진다(spec 「공개는 폴더가 아니라 대상 파일에 건다」).

`granted_by` 는 파일 소유자로 남긴다. 상속·마이그레이션이 원인이라 행위자가 따로 없고,
소유자는 그 파일을 발행할 수 있는 사람이라 사후에 감사 로그를 읽을 때 가장 자연스럽다.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_wiki_single_axis"
down_revision: str | None = "0016_wiki_disabled_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 유효 위키가 켜진 파일 중 질의 가능한 트리가 있는 것. 0016 의 판정 CTE 와 같은 규칙이고
# 방향만 반대다(거기서는 꺼진 것을 찾았다).
_ENABLED_WITH_TREE = """
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
    SELECT w.file_id
    FROM wiki_documents w
    JOIN files f ON f.id = w.file_id
    WHERE f.is_deleted = FALSE
      AND w.status IN ('ready', 'stale')
      AND COALESCE(
            (SELECT n.wiki_enabled FROM nearest n WHERE n.target = w.file_id),
            FALSE
          ) IS TRUE
"""


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not (_has_table("wiki_documents") and _has_table("file_group_permissions")):
        return

    bind = op.get_bind()
    group_id = bind.execute(
        sa.text("SELECT id FROM groups WHERE is_system = TRUE AND is_active = TRUE LIMIT 1")
    ).scalar()
    if group_id is None:
        # `@전사` 그룹이 없으면 채울 대상이 없다 — 0015 가 만들기 전 상태이거나 셋업 전이다.
        return

    op.execute(
        sa.text(
            f"""
            INSERT INTO file_group_permissions
                (file_id, group_id, permission, inherit_to_children, granted_by, granted_at)
            SELECT f.id, :gid, 'read', FALSE, f.user_id, NOW()
            FROM files f
            WHERE f.id IN ({_ENABLED_WITH_TREE})
            ON CONFLICT ON CONSTRAINT uq_file_group_permissions_file_group
            DO NOTHING
            """
        ).bindparams(gid=group_id)
    )


def downgrade() -> None:
    """이 리비전이 넣은 행만 지운다 — 손으로 준 공개 권한은 건드리지 않는다.

    구분 근거는 `inherit_to_children = FALSE` + 위키가 색인한 파일이라는 조합이다. 권한 화면에서
    준 것과 완전히 구분되지는 않지만(부여 출처를 저장하지 않는다), 되돌리기의 목적은 축을 합친
    변경을 물리는 것이라 위키가 다룬 파일 범위로 한정하는 것이 맞다.
    """
    if not (_has_table("wiki_documents") and _has_table("file_group_permissions")):
        return

    bind = op.get_bind()
    group_id = bind.execute(
        sa.text("SELECT id FROM groups WHERE is_system = TRUE AND is_active = TRUE LIMIT 1")
    ).scalar()
    if group_id is None:
        return

    op.execute(
        sa.text(
            """
            DELETE FROM file_group_permissions p
            WHERE p.group_id = :gid
              AND p.inherit_to_children = FALSE
              AND p.file_id IN (SELECT file_id FROM wiki_documents)
            """
        ).bindparams(gid=group_id)
    )
