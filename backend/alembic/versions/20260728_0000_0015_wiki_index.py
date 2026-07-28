"""위키 인덱싱 — files 위키 컬럼, wiki_documents, @전사 시스템 그룹 (spec/wiki-index.md)

Revision ID: 0015_wiki_index
Revises: 0014_routine_day_of_month
Create Date: 2026-07-28

설계는 `spec/wiki-index.md`. 요점은 **위키가 권한 체계를 새로 만들지 않는다**는 것이다 —
위키 토글은 "인덱싱하는가"만 뜻하고, 누가 질의할 수 있는지는 그 파일의 기존 권한이 결정한다.
전사 공개는 `@전사` 시스템 그룹에 read 를 부여하는 것이며, 그래서 새 권한 테이블이 없다.

  files.wiki_enabled      BOOLEAN NULL  -- 3상태: NULL=상속 / TRUE=명시 ON / FALSE=명시 OFF
  files.wiki_disabled_at  TIMESTAMPTZ   -- 유예 삭제 기준 (purger 가 트리를 지우는 시각 계산)
  groups.is_system        BOOLEAN       -- 사용자가 편집/삭제할 수 없는 그룹
  wiki_documents          문서당 트리 1건 (file_id UNIQUE)

`@전사` 그룹은 owner_user_id 가 NOT NULL 이라 관리자가 필요하다. 신규 설치는 셋업 위저드
시점(`create_admin_account`)에 만들어지므로, 이 마이그레이션은 **이미 관리자가 있는 기존
배포에만** 행을 넣는다. 멤버십은 물질화하지 않는다 — `get_user_group_ids` 가 활성 사용자에게
이 그룹 id 를 항상 포함시킨다(가입 훅·멤버십 드리프트 없음).

멱등성(0001~0014 가드 패턴): 통합 테스트가 dev DB 를 `Base.metadata.create_all` 로 out-of-band
재생성(alembic_version 미갱신)한다. 기동 시 `alembic upgrade head` 가 이미 있는 객체를 다시
만들려다 깨지지 않도록 테이블/컬럼별로 존재 여부를 확인해 건너뛴다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_wiki_index"
down_revision: str | None = "0014_routine_day_of_month"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL_USERS_GROUP_NAME = "@전사"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("files", "wiki_enabled"):
        op.add_column("files", sa.Column("wiki_enabled", sa.Boolean(), nullable=True))
    if not _has_column("files", "wiki_disabled_at"):
        op.add_column(
            "files",
            sa.Column("wiki_disabled_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _has_column("groups", "is_system"):
        op.add_column(
            "groups",
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if not _has_table("wiki_documents"):
        op.create_table(
            "wiki_documents",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("tree", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("file_id", name="uq_wiki_documents_file"),
        )
        op.create_index("idx_wiki_documents_status", "wiki_documents", ["status"])

    # 기존 배포용 @전사 부트스트랩. 관리자가 없으면(신규 설치) 건너뛰고, 셋업 위저드가 만든다.
    # 이름 UNIQUE 는 활성 그룹 한정 부분 인덱스(uq_groups_name_active)라 ON CONFLICT 로 막는다.
    op.execute(
        sa.text(
            """
            INSERT INTO groups (name, description, owner_user_id, is_active, is_system)
            SELECT :name, :desc, u.id, TRUE, TRUE
            FROM users u
            WHERE u.role IN ('admin', 'super_admin')
            ORDER BY u.id ASC
            LIMIT 1
            ON CONFLICT DO NOTHING
            """
        ).bindparams(
            name=ALL_USERS_GROUP_NAME,
            desc="전 구성원. 위키 전사 공개에 쓰는 시스템 그룹으로, 멤버십은 자동입니다.",
        )
    )


def downgrade() -> None:
    # 이 그룹으로 부여된 권한(전사 공개)은 그룹이 사라지면 의미를 잃으므로 함께 지운다.
    op.execute(
        sa.text(
            "DELETE FROM file_group_permissions WHERE group_id IN "
            "(SELECT id FROM groups WHERE is_system AND name = :name)"
        ).bindparams(name=ALL_USERS_GROUP_NAME)
    )
    op.execute(
        sa.text("DELETE FROM groups WHERE is_system AND name = :name").bindparams(
            name=ALL_USERS_GROUP_NAME
        )
    )

    if _has_table("wiki_documents"):
        op.drop_index("idx_wiki_documents_status", table_name="wiki_documents")
        op.drop_table("wiki_documents")
    if _has_column("groups", "is_system"):
        op.drop_column("groups", "is_system")
    if _has_column("files", "wiki_disabled_at"):
        op.drop_column("files", "wiki_disabled_at")
    if _has_column("files", "wiki_enabled"):
        op.drop_column("files", "wiki_enabled")
