"""Explicit pending entitlement and receipt metadata; no cash or receipt inference."""
from alembic import op
import sqlalchemy as sa

revision = 'a5receipt'
down_revision = 'z3o4p5q6r7s8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('dividends') as batch:
        batch.add_column(sa.Column('receipt_status', sa.String(24), nullable=False, server_default='legacy_unknown'))
        batch.add_column(sa.Column('entitlement_key', sa.String(64), nullable=True))
        batch.add_column(sa.Column('payment_date', sa.Date(), nullable=True))
        batch.add_column(sa.Column('payment_date_source', sa.String(32), nullable=True))
        batch.add_column(sa.Column('receipt_date', sa.Date(), nullable=True))
        batch.add_column(sa.Column('receipt_account_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('receipt_confirmed_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('revision', sa.Integer(), nullable=False, server_default='1'))
        batch.add_column(sa.Column('source_correction', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('review_reason', sa.String(250), nullable=True))
        batch.create_unique_constraint('uq_dividends_entitlement_key', ['entitlement_key'])
        batch.create_foreign_key('fk_dividends_receipt_account', 'broker_account', ['receipt_account_id'], ['id'], ondelete='RESTRICT')
        batch.create_check_constraint('ck_dividends_receipt_status', "receipt_status IN ('legacy_unknown', 'pending', 'confirmed', 'unresolved')")
        batch.create_check_constraint('ck_dividends_confirmed_receipt', "receipt_status <> 'confirmed' OR (receipt_date IS NOT NULL AND receipt_account_id IS NOT NULL AND receipt_confirmed_at IS NOT NULL)")
        batch.drop_constraint('ck_dividends_amount_positive', type_='check')
        batch.create_check_constraint('ck_dividends_amount_positive', 'amount > 0 OR (amount = 0 AND stock_dividend_shares > 0)')


def downgrade():
    # Old code would auto-post pending rows on ex-date. Refuse a lossy rollback;
    # operator must export/resolve new records under separate authorization.
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM dividends WHERE receipt_status <> 'legacy_unknown' OR entitlement_key IS NOT NULL")).scalar()
    if count:
        raise RuntimeError('Cannot downgrade while new entitlement/receipt records exist')
    with op.batch_alter_table('dividends') as batch:
        batch.drop_constraint('uq_dividends_entitlement_key', type_='unique')
        batch.drop_constraint('fk_dividends_receipt_account', type_='foreignkey')
        batch.drop_constraint('ck_dividends_receipt_status', type_='check')
        batch.drop_constraint('ck_dividends_confirmed_receipt', type_='check')
        batch.drop_constraint('ck_dividends_amount_positive', type_='check')
        batch.create_check_constraint('ck_dividends_amount_positive', 'amount > 0')
        for name in ['review_reason', 'source_correction', 'revision', 'receipt_confirmed_at', 'receipt_account_id', 'receipt_date', 'payment_date_source', 'payment_date', 'entitlement_key', 'receipt_status']:
            batch.drop_column(name)
