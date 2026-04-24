"""parent registered child username at signup

Revision ID: 0005_parent_registered_child_username
Revises: 0004_user_preferences_extended_fields
Create Date: 2026-04-23 14:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_parent_registered_child_username"
down_revision: Union[str, Sequence[str], None] = "0004_user_preferences_extended_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "registered_child_username"):
        op.add_column("users", sa.Column("registered_child_username", sa.String(length=64), nullable=True))


def downgrade() -> None:
    if _has_column("users", "registered_child_username"):
        op.drop_column("users", "registered_child_username")
