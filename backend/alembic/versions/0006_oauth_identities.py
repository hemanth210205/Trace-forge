"""oauth identities

Sign in with Google or GitHub: users created through OAuth have no password, and each linked provider account is
stored by its stable provider id (never matched by email alone after the first link).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18 17:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=True)
    op.create_table('oauth_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("provider IN ('google', 'github')", name=op.f('ck_oauth_identities_provider_known')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_oauth_identities_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_identities')),
    sa.UniqueConstraint('provider', 'subject', name=op.f('uq_oauth_identities_provider_subject'))
    )
    op.create_index('ix_oauth_identities_user_id', 'oauth_identities', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_oauth_identities_user_id', table_name='oauth_identities')
    op.drop_table('oauth_identities')
    # Accounts created through OAuth have no password; they cannot exist in the 0005 schema.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=False)
