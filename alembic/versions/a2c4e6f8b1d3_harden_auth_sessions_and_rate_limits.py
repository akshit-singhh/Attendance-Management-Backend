"""Harden refresh token storage and add persistent auth throttling.

Revision ID: a2c4e6f8b1d3
Revises: 97033cf6c501
Create Date: 2026-11-17 19:30:00.000000
"""
from hashlib import sha256
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2c4e6f8b1d3"
down_revision: Union[str, Sequence[str], None] = "97033cf6c501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()

    # Replace bearer refresh secrets with one-way digests in place. Existing
    # clients retain valid refresh tokens; only the database representation changes.
    rows = connection.execute(sa.text('SELECT id, token FROM refreshtoken')).all()
    for row in rows:
        digest = sha256(row.token.encode("utf-8")).hexdigest()
        connection.execute(
            sa.text("UPDATE refreshtoken SET token = :digest WHERE id = :id"),
            {"digest": digest, "id": row.id},
        )

    # Stored OTPs change to SHA-256 digests; expire outstanding codes so they
    # cannot be accepted under the new verification scheme.
    op.execute("DELETE FROM passwordresetotp")
    op.add_column(
        "passwordresetotp",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "authratelimit",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    # Hashes cannot be reversed; existing refresh sessions are intentionally
    # revoked on downgrade rather than leaving unusable digests presented as tokens.
    op.drop_table("authratelimit")
    op.drop_column("passwordresetotp", "failed_attempts")
    op.execute("DELETE FROM refreshtoken")
