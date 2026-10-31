import { LedgerAccount } from '../../models/accounting.model';
import { formatMoney } from './format';

/** Shown in place of a `balance_kind: "valuation"` balance the API could not value (`balance: null`). */
export const VALUATION_UNAVAILABLE = '市值暫不可得';
/** Label next to a valuation balance: the number is a market value, not a ledger sum. */
export const VALUATION_LABEL = '市值';
/** Appended to the label while the valuation shown is older than today's. */
export const STALE_LABEL = '市值（資料較舊）';

type BalanceFields = Pick<LedgerAccount, 'balance' | 'currency'>;
type KindFields = Pick<LedgerAccount, 'balance_kind' | 'stale'>;

/** The balance as text: the formatted amount, or 市值暫不可得 when the API sent null. */
export function balanceText(account: BalanceFields): string {
  return account.balance === null ? VALUATION_UNAVAILABLE : formatMoney(account.balance, account.currency);
}

/** True for a negative balance; a null balance is never negative (no red colour on 市值暫不可得). */
export function balanceNegative(account: Pick<LedgerAccount, 'balance'>): boolean {
  return account.balance !== null && Number(account.balance) < 0;
}

/** 市值 / 市值（資料較舊） for a valuation account with a value; null for ledger balances and unavailable valuations. */
export function valuationHint(account: KindFields & Pick<LedgerAccount, 'balance'>): string | null {
  if (account.balance_kind !== 'valuation' || account.balance === null) {
    return null;
  }
  return account.stale ? STALE_LABEL : VALUATION_LABEL;
}
