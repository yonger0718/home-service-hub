"""國泰 daily statement: settlement date, 應收付 and 淨收付 per trade date (R7 Phase 1)."""
from alembic import op
import sqlalchemy as sa

revision = 'e9settle'
down_revision = 'd8broker'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cathay_statement_day') as batch:
        batch.add_column(sa.Column('settlement_date', sa.Date(), nullable=True))
        batch.add_column(sa.Column('net_amount', sa.Numeric(18, 4), nullable=True))
        batch.add_column(sa.Column('cash_net', sa.Numeric(18, 4), nullable=True))


def downgrade():
    with op.batch_alter_table('cathay_statement_day') as batch:
        batch.drop_column('cash_net')
        batch.drop_column('net_amount')
        batch.drop_column('settlement_date')
