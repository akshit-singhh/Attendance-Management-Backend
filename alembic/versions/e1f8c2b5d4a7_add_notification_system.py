"""add notification system

Revision ID: e1f8c2b5d4a7
Revises: 4b043c33be60
Create Date: 2026-11-03 00:06:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e1f8c2b5d4a7'
down_revision: Union[str, Sequence[str], None] = '4b043c33be60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Notification table
    op.create_table(
        'notification',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('message', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('notification_type', sa.String(), nullable=False, server_default='ANNOUNCEMENT'),
        sa.Column('priority', sa.String(), nullable=False, server_default='NORMAL'),
        sa.Column('scope_type', sa.String(), nullable=False, server_default='UNIVERSITY'),
        sa.Column('scope_id', sa.Integer(), nullable=True),
        sa.Column('target_role', sqlmodel.sql.sqltypes.AutoString(), nullable=True, server_default='ALL'),
        sa.Column('action_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['sender_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notification_title'), 'notification', ['title'], unique=False)
    op.create_index(op.f('ix_notification_sender_id'), 'notification', ['sender_id'], unique=False)
    op.create_index(op.f('ix_notification_created_at'), 'notification', ['created_at'], unique=False)

    # NotificationRecipient table
    op.create_table(
        'notificationrecipient',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('notification_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['notification_id'], ['notification.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notificationrecipient_notification_id'), 'notificationrecipient', ['notification_id'], unique=False)
    op.create_index(op.f('ix_notificationrecipient_user_id'), 'notificationrecipient', ['user_id'], unique=False)
    op.create_index(op.f('ix_notificationrecipient_is_read'), 'notificationrecipient', ['is_read'], unique=False)

    # UserDeviceToken table
    op.create_table(
        'userdevicetoken',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('fcm_token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('device_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='android'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_userdevicetoken_user_id'), 'userdevicetoken', ['user_id'], unique=False)
    op.create_index(op.f('ix_userdevicetoken_fcm_token'), 'userdevicetoken', ['fcm_token'], unique=False)

    # NotificationSetting table
    op.create_table(
        'notificationsetting',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notificationsetting_key'), 'notificationsetting', ['key'], unique=True)


def downgrade() -> None:
    op.drop_table('notificationsetting')
    op.drop_table('userdevicetoken')
    op.drop_table('notificationrecipient')
    op.drop_table('notification')
