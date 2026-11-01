"""0007_create_bot_state

Revision ID: f7b89abcdef0
Revises: e6a789abcdef
Create Date: 2026-11-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b89abcdef0'
down_revision: Union[str, Sequence[str], None] = 'e6a789abcdef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create bot_state table for storing persistent bot metadata (e.g. last_update_id)."""
    op.create_table(
        'bot_state',
        sa.Column('key', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('value', sa.String(length=255), nullable=False),
    )


def downgrade() -> None:
    """Drop bot_state table."""
    op.drop_table('bot_state')
