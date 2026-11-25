"""永豐期貨 statement: day / month / run tables and portfolio_snapshot.futures_equity_twd."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f1futures'
down_revision = 'e9settle'
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
_STATUSES = "('imported', 'duplicate', 'conflict')"

DAY_AMOUNTS = (
    'prev_balance', 'deposits_net', 'fees_and_tax', 'futures_realized', 'premium_net', 'balance',
    'futures_unrealized', 'equity', 'total_equity', 'initial_margin', 'maintenance_margin',
    'securities_collateral', 'excess_margin', 'margin_call', 'additional_margin', 'withdrawable',
)
MONTH_AMOUNTS = (
    'a', 'b', 'g', 'h', 'k', 'm', 'n', 'q', 'p', 'u', 'v', 'w', 'securities_collateral',
    'additional_margin', 'withdrawable', 'long_premium_value', 'short_premium_value', 'total_equity',
)


def _common(table):
    return [
        sa.Column('accepted_sha256', sa.String(64), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('drive_file_id', sa.String(128), nullable=True),
        sa.Column('last_sha256', sa.String(64), nullable=True),
        sa.Column('last_status', sa.String(16), nullable=True),
        sa.Column('last_detail', _JSON, nullable=True),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('findings', _JSON, nullable=True),
        sa.Column('findings_sha', sa.String(64), nullable=True),
        sa.Column('findings_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ack_sha256', sa.String(64), nullable=True),
        sa.Column('ack_status', sa.String(16), nullable=True),
        sa.Column('ack_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ack_findings_sha', sa.String(64), nullable=True),
        sa.CheckConstraint(f'last_status IS NULL OR last_status IN {_STATUSES}', name=f'ck_{table}_last_status'),
        sa.CheckConstraint(f'ack_status IS NULL OR ack_status IN {_STATUSES}', name=f'ck_{table}_ack_status'),
    ]


def upgrade():
    op.create_table(
        'futures_statement_day',
        sa.Column('statement_date', sa.Date(), primary_key=True),
        *[sa.Column(n, sa.Numeric(18, 4), nullable=False) for n in DAY_AMOUNTS],
        sa.Column('deposits', _JSON, nullable=True),
        sa.Column('fills', _JSON, nullable=True),
        sa.Column('fills_total', _JSON, nullable=True),
        sa.Column('positions', _JSON, nullable=True),
        sa.Column('position_lines', _JSON, nullable=True),
        sa.Column('open_positions', sa.Integer(), nullable=True),
        *_common('futures_statement_day'),
    )
    op.create_table(
        'futures_statement_month',
        sa.Column('month', sa.Date(), primary_key=True),
        sa.Column('month_end', sa.Date(), nullable=False),
        *[sa.Column(n, sa.Numeric(18, 4), nullable=False) for n in MONTH_AMOUNTS],
        sa.Column('activity_dates', _JSON, nullable=True),
        sa.Column('open_positions', sa.Integer(), nullable=True),
        *_common('futures_statement_month'),
    )
    op.create_table(
        'futures_statement_run',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(8), nullable=False, unique=True),
        sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('result', sa.String(8), nullable=False),
        sa.Column('code', sa.String(64), nullable=True),
        sa.Column('files_seen', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('posted', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint("kind IN ('latest', 'ok')", name='ck_futures_statement_run_kind'),
        sa.CheckConstraint("result IN ('ok', 'latched', 'failed')", name='ck_futures_statement_run_result'),
    )
    with op.batch_alter_table('portfolio_snapshot') as batch:
        batch.add_column(sa.Column('futures_equity_twd', sa.Numeric(20, 4), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('portfolio_snapshot') as batch:
        batch.drop_column('futures_equity_twd')
    op.drop_table('futures_statement_run')
    op.drop_table('futures_statement_month')
    op.drop_table('futures_statement_day')
