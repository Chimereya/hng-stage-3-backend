"""add profile indexes
Revision ID: 7d905cc41a08
Revises: 885c1578b699
Create Date: 2026-05-04 22:46:37.299526
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '7d905cc41a08'
down_revision: Union[str, Sequence[str], None] = '885c1578b699'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('playing_with_neon')

    op.alter_column('pending_states', 'code_verifier',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.alter_column('pending_states', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True)
    op.create_index(op.f('ix_pending_states_state'), 'pending_states', ['state'], unique=False)

    op.create_index('ix_profiles_country_age', 'profiles', ['country_id', 'age'], unique=False)
    op.create_index('ix_profiles_created_at', 'profiles', ['created_at'], unique=False)
    op.create_index('ix_profiles_gender_age', 'profiles', ['gender', 'age'], unique=False)
    op.create_index('ix_profiles_gender_age_group', 'profiles', ['gender', 'age_group'], unique=False)

    # Step 1 — drop the FK before touching any column types
    op.drop_constraint('refresh_tokens_user_id_fkey', 'refresh_tokens', type_='foreignkey')

    # Step 2 — retype users.id first (the referenced column)
    op.alter_column('users', 'id',
               existing_type=sa.UUID(),
               type_=sa.String(),
               existing_nullable=False,
               postgresql_using='id::text')

    # Step 3 — now retype refresh_tokens.id and user_id
    op.alter_column('refresh_tokens', 'id',
               existing_type=sa.UUID(),
               type_=sa.String(),
               existing_nullable=False,
               postgresql_using='id::text')
    op.alter_column('refresh_tokens', 'user_id',
               existing_type=sa.UUID(),
               type_=sa.String(),
               existing_nullable=False,
               postgresql_using='user_id::text')

    # Step 4 — re-add the FK now that both sides are String
    op.create_foreign_key(
        'refresh_tokens_user_id_fkey',
        'refresh_tokens', 'users',
        ['user_id'], ['id']
    )


def downgrade() -> None:
    # Step 1 — drop FK before reverting types
    op.drop_constraint('refresh_tokens_user_id_fkey', 'refresh_tokens', type_='foreignkey')

    # Step 2 — retype back to UUID
    op.alter_column('users', 'id',
               existing_type=sa.String(),
               type_=sa.UUID(),
               existing_nullable=False,
               postgresql_using='id::uuid')
    op.alter_column('refresh_tokens', 'user_id',
               existing_type=sa.String(),
               type_=sa.UUID(),
               existing_nullable=False,
               postgresql_using='user_id::uuid')
    op.alter_column('refresh_tokens', 'id',
               existing_type=sa.String(),
               type_=sa.UUID(),
               existing_nullable=False,
               postgresql_using='id::uuid')

    # Step 3 — re-add FK
    op.create_foreign_key(
        'refresh_tokens_user_id_fkey',
        'refresh_tokens', 'users',
        ['user_id'], ['id']
    )

    op.drop_index('ix_profiles_gender_age_group', table_name='profiles')
    op.drop_index('ix_profiles_gender_age', table_name='profiles')
    op.drop_index('ix_profiles_created_at', table_name='profiles')
    op.drop_index('ix_profiles_country_age', table_name='profiles')
    op.drop_index(op.f('ix_pending_states_state'), table_name='pending_states')
    op.alter_column('pending_states', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True)
    op.alter_column('pending_states', 'code_verifier',
               existing_type=sa.VARCHAR(),
               nullable=False)
    op.create_table('playing_with_neon',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('name', sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column('value', sa.REAL(), autoincrement=False, nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('playing_with_neon_pkey'))
    )