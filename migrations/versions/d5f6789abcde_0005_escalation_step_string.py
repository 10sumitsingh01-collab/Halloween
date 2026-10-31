"""0005_escalation_step_string

Revision ID: d5f6789abcde
Revises: c4e56789abcd
Create Date: 2026-10-31 22:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f6789abcde'
down_revision: Union[str, Sequence[str], None] = 'c4e56789abcd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: alter EscalationLog.step from Integer to String(64), then convert existing rows."""
    with op.batch_alter_table('escalation_logs', schema=None) as batch_op:
        batch_op.alter_column(
            'step',
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    # Convert existing integer steps to "REMINDER_<number>"
    op.execute("UPDATE escalation_logs SET step = 'REMINDER_' || step WHERE step GLOB '[0-9]*'")


def downgrade() -> None:
    """Downgrade schema: restore EscalationLog.step from String(64) to Integer."""
    # The downgrade must delete any rows whose step is not of the form REMINDER_<number>
    # because non-reminder named steps (e.g. CAREGIVER_UNCONFIRMED, MISSED_ALERT, SNOOZE_RESUME_*)
    # cannot be represented as integers.
    op.execute("DELETE FROM escalation_logs WHERE step NOT GLOB 'REMINDER_[0-9]*'")

    # Convert "REMINDER_<n>" back to integer string "<n>" before altering type
    op.execute("UPDATE escalation_logs SET step = SUBSTR(step, 10) WHERE step GLOB 'REMINDER_[0-9]*'")

    with op.batch_alter_table('escalation_logs', schema=None) as batch_op:
        batch_op.alter_column(
            'step',
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            existing_nullable=False,
        )
