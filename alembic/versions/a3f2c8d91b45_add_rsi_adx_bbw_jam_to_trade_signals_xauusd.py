"""add rsi_h1 adx_h1 bbw jam_utc to trade_signals_xauusd

Revision ID: a3f2c8d91b45
Revises: 5a1a106bc682
Create Date: 2026-05-02

Kolom baru untuk filter ADVANCED (WR ~54%, PF 2.38):
  - rsi_h1  : RSI 14 di timeframe H1
  - adx_h1  : ADX 14 di timeframe H1
  - bbw     : BB Width % M5 (Bollinger Band Width)
  - jam_utc : Jam entry UTC (untuk analisa WR per jam)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f2c8d91b45'
down_revision: Union[str, None] = '5a1a106bc682'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trade_signals_xauusd', sa.Column('rsi_h1',  sa.Float(), nullable=True))
    op.add_column('trade_signals_xauusd', sa.Column('adx_h1',  sa.Float(), nullable=True))
    op.add_column('trade_signals_xauusd', sa.Column('bbw',     sa.Float(), nullable=True))
    op.add_column('trade_signals_xauusd', sa.Column('jam_utc', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('trade_signals_xauusd', 'jam_utc')
    op.drop_column('trade_signals_xauusd', 'bbw')
    op.drop_column('trade_signals_xauusd', 'adx_h1')
    op.drop_column('trade_signals_xauusd', 'rsi_h1')
