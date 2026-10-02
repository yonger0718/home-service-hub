from sqlalchemy import select

from app.models import Account, LedgerEntry
from app.services.moze_csv import parse_moze_csv
from app.services.moze_import_service import replace_ledger
from app.services.transfer_pairing import pair_transfers


def _import(session, data: bytes, renames=None) -> dict:
    parsed = parse_moze_csv(data)
    summary = replace_ledger(session, parsed, pair_transfers(parsed.rows), None, renames)
    session.commit()
    return summary


def _entries(session, account_name: str) -> list[LedgerEntry]:
    return list(
        session.scalars(
            select(LedgerEntry)
            .join(Account, Account.id == LedgerEntry.account_id)
            .where(Account.name == account_name)
            .order_by(LedgerEntry.seq)
        )
    )
