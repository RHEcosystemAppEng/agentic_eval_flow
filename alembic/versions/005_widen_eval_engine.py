"""Widen evaluation_runs.eval_engine to String(50).

Revision ID: 005
Revises: 004
Create Date: 2026-09-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "evaluation_runs",
        "eval_engine",
        existing_type=sa.String(length=10),
        type_=sa.String(length=50),
        existing_nullable=False,
        existing_server_default=None,
    )


def downgrade() -> None:
    op.alter_column(
        "evaluation_runs",
        "eval_engine",
        existing_type=sa.String(length=50),
        type_=sa.String(length=10),
        existing_nullable=False,
        existing_server_default=None,
    )
