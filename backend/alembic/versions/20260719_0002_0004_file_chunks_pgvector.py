"""file_chunks (RAG 인덱스) + pgvector 확장 + files.indexing_excluded (PRD 5.13/5.2, Phase 7-1)

Revision ID: 0004_file_chunks_pgvector
Revises: 0003_signup_codes_app_settings
Create Date: 2026-07-19

pgvector 확장을 생성하고 RAG 인덱스 테이블(file_chunks)과 인덱싱 제외 플래그
(files.indexing_excluded)를 추가한다. 임베딩은 4096차원 고정(PRD 3.7.4)이며, HNSW 는
2000차원 제한으로 만들지 않는다(사전 필터 전제의 정확 검색, PRD 5.13).

멱등성(0001~0003 가드 패턴): 통합 테스트가 dev DB 에 `Base.metadata.create_all` 로 테이블을
out-of-band 재생성한다(alembic_version 미갱신). 그 뒤 backend 기동 시 `alembic upgrade head` 가
이미 존재하는 객체를 다시 만들려다 깨지므로 테이블/컬럼별로 존재하면 건너뛴다. CREATE EXTENSION
IF NOT EXISTS 는 그 자체로 멱등하다.

downgrade 는 vector 확장을 drop 하지 않는다 — 다른 곳에서 공유될 수 있는 자원이다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_file_chunks_pgvector"
down_revision: str | None = "0003_signup_codes_app_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 임베딩 차원 — solar-embedding-1-large 기준 4096 (PRD 3.7.4, app.models.file_chunk 와 동일).
EMBEDDING_DIM = 4096


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # ── pgvector 확장 (그 자체로 멱등) ──────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── files.indexing_excluded (PRD 5.2) ──────────────────────────────
    if not _has_column("files", "indexing_excluded"):
        op.add_column(
            "files",
            sa.Column(
                "indexing_excluded",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )

    # ── file_chunks (PRD 5.13) ─────────────────────────────────────────
    if not _has_table("file_chunks"):
        op.create_table(
            "file_chunks",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("file_id", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
            sa.Column("token_count", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "file_id",
                "version",
                "chunk_index",
                name="uq_file_chunks_file_version_index",
            ),
        )
        # 파일별 청크 조회/삭제(재인덱싱·drop)를 위한 보조 인덱스. HNSW 벡터 인덱스는 만들지
        # 않는다(4096차원 제한, PRD 5.13 — 사전 필터 전제의 정확 검색).
        op.create_index("idx_file_chunks_file", "file_chunks", ["file_id"])


def downgrade() -> None:
    # vector 확장은 공유 자원이므로 drop 하지 않는다(PRD 지침).
    if _has_table("file_chunks"):
        op.drop_index("idx_file_chunks_file", table_name="file_chunks")
        op.drop_table("file_chunks")
    if _has_column("files", "indexing_excluded"):
        op.drop_column("files", "indexing_excluded")
