"""who sorted a video into its category: the local model or you

Revision ID: e5a1c93b7d20
Revises: d2b7e41f9a10
Create Date: 2026-10-05 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'e5a1c93b7d20'
down_revision: str | None = 'd2b7e41f9a10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('media_items', sa.Column('sorted_by', sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('media_items') as batch:
        batch.drop_column('sorted_by')
