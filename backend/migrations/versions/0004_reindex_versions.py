"""allow creating a new index version for unchanged source

Revision ID: 0004_reindex_versions
Revises: 0003_reply_attachments
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_reindex_versions"
down_revision: str | Sequence[str] | None = "0003_reply_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_versions "
        "DROP CONSTRAINT IF EXISTS uq_knowledge_source_model"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_versions_source_model "
        "ON knowledge_versions (source_sha256, embedding_model)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_versions_source_model")
    op.execute(
        "ALTER TABLE knowledge_versions "
        "ADD CONSTRAINT uq_knowledge_source_model "
        "UNIQUE (source_sha256, embedding_model)"
    )
