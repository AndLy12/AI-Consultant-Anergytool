"""Создаёт исходную схему аккаунтов, сессий, диалогов, сообщений и заявок."""
from alembic import op
import sqlalchemy as sa

revision = '61c24c2188d1'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Создаём таблицы и ограничения исходной версии схемы.
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_staff', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('auth_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_auth_sessions'))
    )
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auth_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('dialogs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.CheckConstraint("status IN ('active', 'closed')", name=op.f('ck_dialogs_status')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_dialogs_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dialogs'))
    )
    with op.batch_alter_table('dialogs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dialogs_user_id'), ['user_id'], unique=False)

    op.create_table('applications',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('dialog_id', sa.String(length=36), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=300), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('consent_at', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'created')", name=op.f('ck_applications_status')),
    sa.ForeignKeyConstraint(['dialog_id'], ['dialogs.id'], name=op.f('fk_applications_dialog_id_dialogs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applications')),
    sa.UniqueConstraint('dialog_id', name=op.f('uq_applications_dialog_id'))
    )
    op.create_table('messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('dialog_id', sa.String(length=36), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('sources', sa.JSON(), nullable=False),
    sa.Column('elapsed_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f('ck_messages_role')),
    sa.ForeignKeyConstraint(['dialog_id'], ['dialogs.id'], name=op.f('fk_messages_dialog_id_dialogs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
    sa.UniqueConstraint('dialog_id', 'request_id', 'role', name=op.f('uq_messages_dialog_id'))
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_dialog_id'), ['dialog_id'], unique=False)

    # Исходная схема создана.

def downgrade():
    # При откате удаляем объекты в обратном порядке зависимостей.
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_dialog_id'))

    op.drop_table('messages')
    op.drop_table('applications')
    with op.batch_alter_table('dialogs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dialogs_user_id'))

    op.drop_table('dialogs')
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_sessions_user_id'))

    op.drop_table('auth_sessions')
    op.drop_table('users')
    # Откат исходной схемы завершён.
