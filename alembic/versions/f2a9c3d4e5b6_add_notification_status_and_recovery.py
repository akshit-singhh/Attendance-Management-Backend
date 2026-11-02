"""add notification delivery status, recovery fields, and recipient unique constraint

Revision ID: f2a9c3d4e5b6
Revises: e1f8c2b5d4a7
Create Date: 2026-11-03 01:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f2a9c3d4e5b6'
down_revision: Union[str, None] = 'e1f8c2b5d4a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum definition
status_enum = postgresql.ENUM('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', name='notificationdeliverystatus', create_type=False)


def upgrade() -> None:
    # 1. Create enum type if not exists
    status_enum.create(op.get_bind(), checkfirst=True)

    # 2. Add status and recovery columns to notification table
    op.add_column('notification', sa.Column('status', status_enum, nullable=False, server_default='PENDING'))
    op.create_index(op.f('ix_notification_status'), 'notification', ['status'], unique=False)
    op.add_column('notification', sa.Column('failure_reason', sa.String(), nullable=True))
    op.add_column('notification', sa.Column('processed_count', sa.Integer(), nullable=False, server_default='0'))

    # Update any existing notifications to COMPLETED
    op.execute("UPDATE notification SET status = 'COMPLETED' WHERE status = 'PENDING'")

    # 3. Add unique constraint to notificationrecipient to guarantee idempotency on retries
    op.execute("""
        DELETE FROM notificationrecipient a USING notificationrecipient b
        WHERE a.id < b.id 
          AND a.notification_id = b.notification_id 
          AND a.user_id = b.user_id;
    """)
    op.create_unique_constraint('uq_notification_recipient', 'notificationrecipient', ['notification_id', 'user_id'])


def downgrade() -> None:
    op.drop_constraint('uq_notification_recipient', 'notificationrecipient', type_='unique')
    op.drop_column('notification', 'processed_count')
    op.drop_column('notification', 'failure_reason')
    op.drop_index(op.f('ix_notification_status'), table_name='notification')
    op.drop_column('notification', 'status')
    status_enum.drop(op.get_bind(), checkfirst=True)
