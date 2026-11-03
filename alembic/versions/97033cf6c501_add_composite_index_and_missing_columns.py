"""add_composite_index_and_missing_columns

Revision ID: 97033cf6c501
Revises: f2a9c3d4e5b6
Create Date: 2026-11-04 02:42:40.678231

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '97033cf6c501'
down_revision: Union[str, Sequence[str], None] = 'f2a9c3d4e5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Ensure enum and missing columns exist for empty database setup
    op.execute("ALTER TYPE scopetypeenum ADD VALUE IF NOT EXISTS 'SECTION';")
    op.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS employee_code VARCHAR;')
    op.execute('CREATE INDEX IF NOT EXISTS ix_user_employee_code ON "user" (employee_code);')
    op.execute("ALTER TABLE subject ADD COLUMN IF NOT EXISTS credits INTEGER DEFAULT 3;")
    op.execute("ALTER TABLE subject ADD COLUMN IF NOT EXISTS course_type VARCHAR DEFAULT 'LECTURE';")
    op.execute("ALTER TABLE courseoffering ADD COLUMN IF NOT EXISTS weekly_hours REAL DEFAULT NULL;")
    op.execute("ALTER TABLE courseoffering ADD COLUMN IF NOT EXISTS course_type VARCHAR DEFAULT NULL;")

    # 2. Composite index on notificationrecipient (user_id, is_read) for ultra-fast inbox queries
    op.execute("CREATE INDEX IF NOT EXISTS ix_notificationrecipient_user_id_is_read ON notificationrecipient (user_id, is_read);")

    # 3. Medicalleave table
    op.execute("""
        CREATE TABLE IF NOT EXISTS medicalleave (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES "user"(id),
            leave_date DATE NOT NULL,
            reason VARCHAR,
            created_at TIMESTAMP WITHOUT TIME ZONE
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_medicalleave_user_id ON medicalleave (user_id);")

    # 4. Batch indexes
    op.execute("CREATE INDEX IF NOT EXISTS ix_batch_name ON batch (name);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notificationrecipient_user_id_is_read;")
    op.execute("DROP INDEX IF EXISTS ix_batch_name;")
    op.execute("DROP TABLE IF EXISTS medicalleave;")
    op.execute("DROP INDEX IF EXISTS ix_user_employee_code;")
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS employee_code;')
