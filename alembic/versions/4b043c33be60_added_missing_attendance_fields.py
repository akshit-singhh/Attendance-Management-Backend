"""added missing attendance fields

Revision ID: 4b043c33be60
Revises: b891e4f2c678
Create Date: 2026-09-28 02:05:44.557497

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4b043c33be60'
down_revision: Union[str, Sequence[str], None] = 'b891e4f2c678'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Use raw SQL to force PostgreSQL to create the Enum type
    op.execute("CREATE TYPE leavetype AS ENUM ('MEDICAL', 'DUTY', 'SPECIAL')")

    # 2. Add AttendanceRecord columns
    op.add_column('attendancerecord', sa.Column('course_offering_id', sa.Integer(), nullable=False))
    op.add_column('attendancerecord', sa.Column('marked_by_id', sa.Integer(), nullable=False))
    op.add_column('attendancerecord', sa.Column('date', sa.Date(), nullable=False))
    op.create_foreign_key(None, 'attendancerecord', 'courseoffering', ['course_offering_id'], ['id'])
    op.create_foreign_key(None, 'attendancerecord', 'user', ['marked_by_id'], ['id'])
    
    # 3. Add LeaveRequest column, telling SQLAlchemy NOT to try creating the type again (create_type=False)
    op.add_column(
        'leaverequest', 
        sa.Column('leave_type', postgresql.ENUM('MEDICAL', 'DUTY', 'SPECIAL', name='leavetype', create_type=False), nullable=False)
    )


def downgrade() -> None:
    # 1. Drop columns
    op.drop_column('leaverequest', 'leave_type')
    op.drop_constraint(None, 'attendancerecord', type_='foreignkey')
    op.drop_constraint(None, 'attendancerecord', type_='foreignkey')
    op.drop_column('attendancerecord', 'date')
    op.drop_column('attendancerecord', 'marked_by_id')
    op.drop_column('attendancerecord', 'course_offering_id')
    
    # 2. Drop the Enum type using raw SQL
    op.execute("DROP TYPE leavetype")