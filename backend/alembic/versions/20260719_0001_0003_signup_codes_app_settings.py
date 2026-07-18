"""signup_codes + app_settings + users.status 이관 (PRD 5.11/5.12, 가입 코드제 전환)

Revision ID: 0003_signup_codes_app_settings
Revises: 0002_upload_sessions
Create Date: 2026-07-19

가입 승인제 → 가입 코드제 전환(2026-07-19):
- signup_codes(5.11)·app_settings(5.12) 테이블 신설.
- users.status: 구 승인제 값 pending/rejected 를 inactive 로 이관하고 기본값을 active 로 변경.

멱등성(0001/0002 가드 패턴): 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 테이블을
out-of-band 재생성한다(alembic_version 미갱신). 그 뒤 backend 기동 시 `alembic upgrade head` 가
이미 존재하는 테이블을 다시 만들려다 깨지므로 테이블별로 존재하면 건너뛴다. users.status 의
ALTER DEFAULT 와 데이터 이관 UPDATE 는 반복 실행해도 안전하므로 가드 없이 그대로 수행한다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_signup_codes_app_settings"
down_revision: str | None = "0002_upload_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # ── signup_codes (PRD 5.11) ───────────────────────────────────────
    if not _has_table("signup_codes"):
        op.create_table(
            "signup_codes",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column(
                "memo", sa.String(length=200), server_default=sa.text("''"), nullable=False
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("max_uses", sa.Integer(), nullable=True),
            sa.Column(
                "use_count", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
            sa.Column(
                "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
            ),
            sa.Column("created_by", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_signup_codes_code"),
        )

    # ── app_settings (PRD 5.12) ───────────────────────────────────────
    if not _has_table("app_settings"):
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column(
                "value", postgresql.JSONB(astext_type=sa.Text()), nullable=False
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("key"),
        )

    # ── users.status 이관 (승인제 → 코드제) ────────────────────────────
    # 반복 실행 안전: 기본값을 active 로 바꾸고, 남아있는 구 승인제 값을 inactive 로 이관한다.
    op.alter_column(
        "users", "status", server_default=sa.text("'active'"), existing_type=sa.String(20)
    )
    op.execute(
        "UPDATE users SET status = 'inactive' WHERE status IN ('pending', 'rejected')"
    )


def downgrade() -> None:
    # 기본값만 되돌린다. inactive→pending 은 원본 구분이 불가능하므로 데이터는 이관하지 않는다.
    op.alter_column(
        "users", "status", server_default=sa.text("'pending'"), existing_type=sa.String(20)
    )
    if _has_table("app_settings"):
        op.drop_table("app_settings")
    if _has_table("signup_codes"):
        op.drop_table("signup_codes")
