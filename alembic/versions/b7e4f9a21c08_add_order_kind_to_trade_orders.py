"""add_order_kind_to_trade_orders

Revision ID: b7e4f9a21c08
Revises: a3f2c8d91b45
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4f9a21c08'
down_revision: Union[str, None] = 'a3f2c8d91b45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # order_kind: market / limit / stop — bedakan tipe pending order
    op.add_column(
        'trade_orders',
        sa.Column('order_kind', sa.String(length=10), nullable=True, server_default='market'),
    )


def downgrade() -> None:
    op.drop_column('trade_orders', 'order_kind')
