import { LedgerEntry } from '../../../models/accounting.model';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals } from '../format';
import { CurrencyNet } from '../entry-form/split-summary';

/** A split group's net per account currency, from the server-signed stored amounts (no client FX conversion). */
export function storedGroupNet(members: readonly LedgerEntry[]): CurrencyNet[] {
  const totals = new Map<string, number>();
  for (const member of members) {
    totals.set(member.currency, (totals.get(member.currency) ?? 0) + Number(member.amount));
  }
  return [...totals].map(([currency, amount]) => ({ currency, amount: roundHalfAway(amount, currencyDecimals(currency)) }));
}
