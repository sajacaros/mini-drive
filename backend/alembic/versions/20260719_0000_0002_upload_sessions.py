"""upload_sessions (재개 가능 업로드 세션, PRD 3.2)

Revision ID: 0002_upload_sessions
Revises: 0001_initial_schema
Create Date: 2026-07-19

S3 Multipart Upload 기반 재개 업로드의 세션 메타데이터 테이블.
file_id 는 kind 에 따라 미리 선점한 files.id('new') 또는 기존 files.id('version')를
가리키며 FK 를 걸지 않는다(모델 docstring 참조).

멱등성: 이 프로젝트는 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 전체 테이블을
out-of-band 재생성한다(alembic_version 은 갱신하지 않음). 그 뒤 backend 기동 시
`alembic upgrade head` 가 이미 존재하는 테이블을 다시 만들려다 깨지므로, 존재하면 건너뛴다.
(테이블의 유일한 out-of-band 생성원은 동일 모델의 create_all 이라 스키마 일치가 보장된다.)
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_upload_sessions"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # 이미 out-of-band(create_all 등)로 생성됐으면 멱등하게 건너뛴다(모듈 docstring 참조).
    if _has_table("upload_sessions"):
        return
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("upload_id", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("total_size", sa.BigInteger(), nullable=False),
        sa.Column("part_size", sa.BigInteger(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_upload_sessions_user", "upload_sessions", ["user_id"], unique=False
    )
    op.create_index(
        "idx_upload_sessions_expires", "upload_sessions", ["expires_at"], unique=False
    )


def downgrade() -> None:
    if not _has_table("upload_sessions"):
        return
    op.drop_index("idx_upload_sessions_expires", table_name="upload_sessions")
    op.drop_index("idx_upload_sessions_user", table_name="upload_sessions")
    op.drop_table("upload_sessions")
