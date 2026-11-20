"""enable pgvector extension

Revision ID: 0002_enable_pgvector
Revises: 0001_initial
Create Date: 2026-11-21

The extension is provisioned by the PostgreSQL image. The migration is kept
separate so relational-only test databases can stop at 0001 when pgvector is
not compiled into the local PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_enable_pgvector"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
