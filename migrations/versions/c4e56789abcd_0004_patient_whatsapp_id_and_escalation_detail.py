"""0004_patient_whatsapp_id_and_escalation_detail

Revision ID: c4e56789abcd
Revises: a1e23456789b
Create Date: 2026-10-31 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e56789abcd'
down_revision: Union[str, Sequence[str], None] = 'a1e23456789b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add Patient.whatsapp_id and EscalationLog.detail."""
    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('whatsapp_id', sa.String(), nullable=True))

    with op.batch_alter_table('escalation_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('detail', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('escalation_logs', schema=None) as batch_op:
        batch_op.drop_column('detail')

    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.drop_column('whatsapp_id')
