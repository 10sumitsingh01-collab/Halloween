"""0008_create_alert_logs

Revision ID: 0008abcdef01
Revises: f7b89abcdef0
Create Date: 2026-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008abcdef01'
down_revision: Union[str, Sequence[str], None] = 'f7b89abcdef0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create alert_logs table for caregiver assistant alerts."""
    op.create_table(
        'alert_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('caregivers_notified', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=64), server_default='sent', nullable=False),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('alert_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alert_logs_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alert_logs_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alert_logs_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_alert_logs_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    """Drop alert_logs table."""
    with op.batch_alter_table('alert_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alert_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_alert_logs_category'))
        batch_op.drop_index(batch_op.f('ix_alert_logs_patient_id'))
        batch_op.drop_index(batch_op.f('ix_alert_logs_id'))

    op.drop_table('alert_logs')
