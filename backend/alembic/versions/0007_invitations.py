"""invitations

Invite-only registration (TRACEFORGE_REGISTRATION=invite): one row per invitation, tied to one email address. Only a
SHA-256 hash of the invitation code is stored. Additive: no existing table or row is changed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18 19:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('invitations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('note', sa.String(length=200), server_default=sa.text("''"), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('used_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_invitations_email_lowercase')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_invitations_expires_after_created')),
    sa.ForeignKeyConstraint(['used_by_user_id'], ['users.id'], name=op.f('fk_invitations_used_by_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitations')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_invitations_token_hash'))
    )
    op.create_index('ix_invitations_email', 'invitations', ['email'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_invitations_email', table_name='invitations')
    op.drop_table('invitations')
