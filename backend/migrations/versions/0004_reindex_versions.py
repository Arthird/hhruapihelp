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
    op.drop_constraint("uq_knowledge_source_model", "knowledge_versions", type_="unique")
    op.create_index(
        "ix_knowledge_versions_source_model",
        "knowledge_versions",
        ["source_sha256", "embedding_model"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_versions_source_model", table_name="knowledge_versions")
    op.create_unique_constraint(
        "uq_knowledge_source_model",
        "knowledge_versions",
        ["source_sha256", "embedding_model"],
    )
