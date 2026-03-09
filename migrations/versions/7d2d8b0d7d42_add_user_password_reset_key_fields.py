"""add user password reset key fields

Revision ID: 7d2d8b0d7d42
Revises: 49b261cbdeef
Create Date: 2026-03-08 20:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7d2d8b0d7d42"
down_revision = "49b261cbdeef"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "password_reset_key_hash",
                sa.String(length=255),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "password_reset_key_icon",
                sa.String(length=40),
                nullable=False,
                server_default="bi-key-fill",
            )
        )


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("password_reset_key_icon")
        batch_op.drop_column("password_reset_key_hash")
