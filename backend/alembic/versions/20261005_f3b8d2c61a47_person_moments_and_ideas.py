"""gifts and moments with people; ideas found in the journal

Revision ID: f3b8d2c61a47
Revises: e5a1c93b7d20
Create Date: 2026-10-05 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'f3b8d2c61a47'
down_revision: str | None = 'e5a1c93b7d20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('person_moments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('person_id', sa.Integer(), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('journal_entry_id', sa.Integer(), nullable=True),
    sa.Column('is_private', sa.Boolean(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('person_moments', schema=None) as batch_op:
        batch_op.create_index('ix_person_moments_person_date', ['person_id', 'occurred_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_person_moments_user_id'), ['user_id'], unique=False)

    op.create_table('ideas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('journal_entry_id', sa.Integer(), nullable=True),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('day_number', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('domain', sa.String(length=30), nullable=False),
    sa.Column('note_id', sa.Integer(), nullable=True),
    sa.Column('new_essay', sa.String(length=200), nullable=True),
    sa.Column('refs', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('placed_note_id', sa.Integer(), nullable=True),
    sa.Column('placed_text', sa.Text(), nullable=True),
    sa.Column('placed_at', sa.DateTime(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=300), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['note_id'], ['notes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['placed_note_id'], ['notes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'fingerprint')
    )
    with op.batch_alter_table('ideas', schema=None) as batch_op:
        batch_op.create_index('ix_ideas_user_status', ['user_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_ideas_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ideas', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ideas_user_id'))
        batch_op.drop_index('ix_ideas_user_status')
    op.drop_table('ideas')
    with op.batch_alter_table('person_moments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_person_moments_user_id'))
        batch_op.drop_index('ix_person_moments_person_date')
    op.drop_table('person_moments')
