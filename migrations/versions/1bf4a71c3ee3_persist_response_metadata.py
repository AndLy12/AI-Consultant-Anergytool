"""Добавляет хранение метаданных ответа ядра вместе с сообщением."""
from alembic import op
import sqlalchemy as sa

revision = '1bf4a71c3ee3'
down_revision = '61c24c2188d1'
branch_labels = None
depends_on = None

def upgrade():
    # Добавляем поле для действия, статуса и остальных метаданных ответа.
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('result_meta', sa.JSON(), nullable=False, server_default='{}'))

    # Изменение схемы применено.

def downgrade():
    # При откате удаляем добавленное поле метаданных.
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_column('result_meta')

    # Откат изменения завершён.
