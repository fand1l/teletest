"""add hnsw index on messages.embedding

Revision ID: b3f1c9a20d41
Revises: 087802aeab7c
Create Date: 2026-07-16 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b3f1c9a20d41'
down_revision: Union[str, Sequence[str], None] = '087802aeab7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Adds an HNSW index for cosine similarity search on messages.embedding.
    Without it every find_similar() call is a sequential scan over the
    whole table, which degrades linearly as messages accumulate.
    """
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_embedding_hnsw "
        "ON messages USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_embedding_hnsw")
