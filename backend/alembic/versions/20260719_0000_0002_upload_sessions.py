"""upload_sessions (재개 가능 업로드 세션, PRD 3.2)

Revision ID: 0002_upload_sessions
Revises: 0001_initial_schema
Create Date: 2026-07-19

S3 Multipart Upload 기반 재개 업로드의 세션 메타데이터 테이블.
file_id 는 kind 에 따라 미리 선점한 files.id('new') 또는 기존 files.id('version')를
가리키며 FK 를 걸지 않는다(모델 docstring 참조).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_upload_sessions"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    op.drop_index("idx_upload_sessions_expires", table_name="upload_sessions")
    op.drop_index("idx_upload_sessions_user", table_name="upload_sessions")
    op.drop_table("upload_sessions")
