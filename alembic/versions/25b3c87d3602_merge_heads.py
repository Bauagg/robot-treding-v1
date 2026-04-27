"""merge_heads

Revision ID: 25b3c87d3602
Revises: 1263176b5cda, 5bee066f336e
Create Date: 2026-04-27 19:38:51.618842

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25b3c87d3602'
down_revision: Union[str, None] = ('1263176b5cda', '5bee066f336e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
