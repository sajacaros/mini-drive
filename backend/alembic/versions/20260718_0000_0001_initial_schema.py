"""initial schema (PRD 5장 전체 스키마)

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-18

8개 테이블: users, groups, files, file_versions, shares,
group_members, file_group_permissions, audit_logs.
FK 의존성 순서로 생성하고 downgrade 는 역순으로 삭제한다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── users (PRD 5.1) ───────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "display_name",
            sa.String(length=100),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column(
            "role", sa.String(length=20), server_default=sa.text("'user'"), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "storage_used", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "max_storage",
            sa.BigInteger(),
            server_default=sa.text("10737418240"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    # ── groups (PRD 5.5) ──────────────────────────────────────────────
    op.create_table(
        "groups",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_groups_name"),
    )

    # ── files (PRD 5.2) ───────────────────────────────────────────────
    op.create_table(
        "files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("parent_folder_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("file_key", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("thumbnail_key", sa.String(length=500), nullable=True),
        sa.Column(
            "is_folder", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "base_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "current_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"]),
        sa.ForeignKeyConstraint(["parent_folder_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # 같은 폴더 내 이름 중복 방지 — 휴지통(soft-deleted) 제외 (PRD 5.2).
    op.create_index(
        "uq_files_sibling_name",
        "files",
        ["parent_folder_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_deleted = FALSE"),
    )
    op.create_index("idx_files_parent", "files", ["parent_folder_id"], unique=False)
    op.create_index("idx_files_user", "files", ["user_id"], unique=False)

    # ── file_versions (PRD 5.3) ───────────────────────────────────────
    op.create_table(
        "file_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id", "version", name="uq_file_versions_file_version"
        ),
    )

    # ── shares (PRD 5.4) ──────────────────────────────────────────────
    op.create_table(
        "shares",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("share_url", sa.String(length=64), nullable=False),
        sa.Column(
            "permission",
            sa.String(length=20),
            server_default=sa.text("'read'"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_downloads", sa.Integer(), nullable=True),
        sa.Column(
            "download_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_url"),
    )

    # ── group_members (PRD 5.6) ───────────────────────────────────────
    op.create_table(
        "group_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id", "user_id", name="uq_group_members_group_user"
        ),
    )

    # ── file_group_permissions (PRD 5.7) ──────────────────────────────
    op.create_table(
        "file_group_permissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "permission",
            sa.String(length=20),
            server_default=sa.text("'read'"),
            nullable=False,
        ),
        sa.Column(
            "inherit_to_children",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id", "group_id", name="uq_file_group_permissions_file_group"
        ),
    )

    # ── audit_logs (PRD 5.9) ──────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_audit_logs_target", "audit_logs", ["target_type", "target_id"], unique=False
    )
    op.create_index(
        "idx_audit_logs_actor", "audit_logs", ["actor_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_audit_logs_actor", table_name="audit_logs")
    op.drop_index("idx_audit_logs_target", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_table("file_group_permissions")
    op.drop_table("group_members")
    op.drop_table("shares")
    op.drop_table("file_versions")

    op.drop_index("idx_files_user", table_name="files")
    op.drop_index("idx_files_parent", table_name="files")
    op.drop_index("uq_files_sibling_name", table_name="files")
    op.drop_table("files")

    op.drop_table("groups")
    op.drop_table("users")
