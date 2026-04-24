"""task ai metadata

Revision ID: 0002_task_ai_metadata
Revises: 0001_initial_schema
Create Date: 2026-04-22 11:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_task_ai_metadata"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("source_type", sa.String(length=16), nullable=False, server_default="text"))
    op.add_column("tasks", sa.Column("parse_confidence", sa.Float(), nullable=True))
    op.add_column("tasks", sa.Column("parse_raw_input", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("ai_suggestions_json", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index(op.f("ix_tasks_source_type"), "tasks", ["source_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_source_type"), table_name="tasks")
    op.drop_column("tasks", "updated_at")
    op.drop_column("tasks", "ai_suggestions_json")
    op.drop_column("tasks", "parse_raw_input")
    op.drop_column("tasks", "parse_confidence")
    op.drop_column("tasks", "source_type")
