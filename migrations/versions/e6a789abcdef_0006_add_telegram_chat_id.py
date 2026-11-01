"""0006_add_telegram_chat_id

Revision ID: e6a789abcdef
Revises: d5f6789abcde
Create Date: 2026-11-01 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6a789abcdef'
down_revision: Union[str, Sequence[str], None] = 'd5f6789abcde'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add nullable telegram_chat_id to patients and caregivers."""
    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_chat_id', sa.String(), nullable=True))

    with op.batch_alter_table('caregivers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_chat_id', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema: remove telegram_chat_id from caregivers and patients."""
    with op.batch_alter_table('caregivers', schema=None) as batch_op:
        batch_op.drop_column('telegram_chat_id')

    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.drop_column('telegram_chat_id')
