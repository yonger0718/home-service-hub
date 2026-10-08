import { LedgerAccount } from '../../../models/accounting.model';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals } from '../format';
import { evalOrNull, isWritableKind, signFor } from './entry-draft';
import { ChildDraft, ParentDraft, nonempty } from './split-draft';

/** A per-account-currency amount; null when it cannot be known on the client (never zero, never an FX sum). */
export interface CurrencyNet {
  currency: string;
  amount: number | null;
}

/**
 * The child's signed amount in its account currency: the stored server sign for protected / non-editable rows,
 * otherwise the typed amount converted (manual amount, or the known rate) and signed by its kind.
 */
export function accountAmount(child: ChildDraft, account: LedgerAccount | undefined): number | null {
  if (child.protected || !isWritableKind(child.kind)) {
    return child.loaded ? Number(child.loaded.signedAmount) : null;
  }
  if (!account) {
    return null;
  }
  const amount = evalOrNull(child.amountExpr, currencyDecimals(child.fx?.original_currency ?? account.currency));
  if (amount === null) {
    return null;
  }
  let converted = amount;
  if (child.fx) {
    if (!child.fx.use_online && child.fx.manual === 'amount' && child.fx.amount !== null) {
      converted = Number(child.fx.amount);
    } else if (child.fx.fx_rate !== null) {
      converted = amount * Number(child.fx.fx_rate);
    } else {
      return null;
    }
  }
  return signFor(child.kind) * roundHalfAway(converted, currencyDecimals(account.currency));
}

function sum(lines: CurrencyNet[]): CurrencyNet[] {
  const values = new Map<string, number | null>();
  for (const line of lines) {
    const unknown = values.has(line.currency) && values.get(line.currency) === null;
    const previous = values.get(line.currency) ?? 0;
    values.set(line.currency, unknown || line.amount === null ? null : previous + line.amount);
  }
  return [...values].map(([currency, amount]) => ({
    currency,
    amount: amount === null ? null : roundHalfAway(amount, currencyDecimals(currency)),
  }));
}

/**
 * The parent's net, one line per account currency. A child with nothing typed yet adds 0; an invalid expression or
 * an unknown conversion makes its currency's line unknown.
 */
export function netByCurrency(children: readonly ChildDraft[], accounts: readonly LedgerAccount[]): CurrencyNet[] {
  return sum(
    children.map(child => {
      const account = accounts.find(candidate => candidate.id === child.accountId);
      const blank = !child.protected && isWritableKind(child.kind) && !child.amountExpr.trim();
      return {
        currency: account?.currency ?? child.loaded?.currency ?? '—',
        amount: blank && account ? 0 : accountAmount(child, account),
      };
    }),
  );
}

/**
 * Estimated rewards of the selected rules, per account currency. Excludes existing reward ledger rows and does not
 * apply statement / window caps (an estimate, not a promise).
 */
export function rewardEstimates(children: readonly ChildDraft[], accounts: readonly LedgerAccount[]): CurrencyNet[] {
  return sum(
    children.flatMap(child => {
      const account = accounts.find(candidate => candidate.id === child.accountId);
      if (!account) {
        return [];
      }
      const amount = accountAmount(child, account);
      return child.availableRules
        .filter(rule => child.ruleIds.includes(rule.id))
        .map(rule => {
          let reward =
            rule.method === 'fixed'
              ? Number(rule.fixed_amount ?? 0)
              : amount === null
                ? null
                : (Math.abs(amount) * Number(rule.rate ?? 0)) / 100;
          if (reward !== null) {
            if (rule.txn_rounding === 'floor') reward = Math.floor(reward);
            if (rule.txn_rounding === 'ceil') reward = Math.ceil(reward);
            if (rule.txn_rounding === 'round') reward = roundHalfAway(reward, 0);
          }
          return { currency: account.currency, amount: reward };
        });
    }),
  );
}

/** Group fields a dissolve will not copy onto the survivor because it has its own (spec §1.5 copy-into-blank). */
export function dissolveNotices(parent: ParentDraft, survivor: ChildDraft): string[] {
  return [
    // A parent name equal to the survivor's (a prefilled convert) is not lost: the survivor keeps it.
    ...(nonempty(parent.name) && nonempty(survivor.name) && parent.name.trim() !== survivor.name.trim()
      ? [`整筆名稱「${parent.name}」將不保留`]
      : []),
    ...(nonempty(parent.merchant) && nonempty(survivor.loaded?.merchant) ? [`整筆商家「${parent.merchant}」將不保留`] : []),
    ...(nonempty(parent.description) && nonempty(survivor.description) ? ['整筆備註將不保留'] : []),
  ];
}
