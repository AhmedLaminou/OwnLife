"""money suggestions found in the journal

Revision ID: d2b7e41f9a10
Revises: c16a0aa9dae3
Create Date: 2026-10-03 21:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'd2b7e41f9a10'
down_revision: str | None = 'c16a0aa9dae3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('money_suggestions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('journal_entry_id', sa.Integer(), nullable=True),
    sa.Column('item', sa.String(length=200), nullable=False),
    sa.Column('amount_minor', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('counterparty', sa.String(length=120), nullable=True),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('transaction_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'fingerprint')
    )
    with op.batch_alter_table('money_suggestions', schema=None) as batch_op:
        batch_op.create_index('ix_money_suggestions_user_status', ['user_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_money_suggestions_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('money_suggestions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_money_suggestions_user_id'))
        batch_op.drop_index('ix_money_suggestions_user_status')

    op.drop_table('money_suggestions')
