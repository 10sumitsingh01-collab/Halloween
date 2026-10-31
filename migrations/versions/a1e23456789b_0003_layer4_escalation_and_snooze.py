"""0003_layer4_escalation_and_snooze

Revision ID: a1e23456789b
Revises: df41660d5b90
Create Date: 2026-10-31 22:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1e23456789b'
down_revision: Union[str, Sequence[str], None] = 'df41660d5b90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for Layer 4: snooze and escalation tracking."""
    with op.batch_alter_table('dose_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('escalation_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recipient', sa.String(), nullable=False, server_default='patient'))
        batch_op.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column(
            'status',
            sa.Enum('PENDING', 'SENT', 'FAILED', name='escalationstatus', native_enum=False),
            nullable=False,
            server_default='pending',
        ))
        batch_op.create_unique_constraint(
            'uq_escalation_event_step_recipient',
            ['dose_event_id', 'step', 'recipient'],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('escalation_logs', schema=None) as batch_op:
        batch_op.drop_constraint('uq_escalation_event_step_recipient', type_='unique')
        batch_op.drop_column('status')
        batch_op.drop_column('attempts')
        batch_op.drop_column('recipient')

    with op.batch_alter_table('dose_events', schema=None) as batch_op:
        batch_op.drop_column('snoozed_until')
