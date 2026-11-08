import { EntryDetail, RewardAccrual, RewardAccrualState } from '../../../models/accounting.model';
import { shortDate } from '../dates';

/** EntryDetail.rewardAmount's signature: a figure in the reward's unit (D1a). */
export type RewardAmountText = (
  value: string, accountId: number, accountName: string, currency: string, sign?: boolean,
) => string;

export interface RewardCard {
  /** `a${accrual.id}` or `r${reward.id}`. */
  key: string;
  /** rule_name; for a reward row without an accrual: reward.name || '紅利回饋'. */
  name: string;
  state: RewardAccrualState;
  /** Formatted; signed (+) unless forfeited. */
  amount: string;
  /** Formatted struck estimate beside the amount (never for forfeited). */
  was: string | null;
  /** The state line. */
  line: string;
  /** Reward row id for 「看回饋記錄 ›」 (posted cards only). */
  link: number | null;
}

function postedLine(date: string | null, account: string, today: string): string {
  if (date === null) {
    return `已入帳到 ${account}`;
  }
  return date > today ? `將於 ${shortDate(date)} 入帳到 ${account}` : `${shortDate(date)} 已入帳到 ${account}`;
}

function accrualCard(detail: EntryDetail, accrual: RewardAccrual, today: string, text: RewardAmountText): RewardCard {
  const unit = (value: string, sign = false): string =>
    text(value, accrual.reward_account_id, accrual.reward_account_name, detail.currency, sign);
  const row = detail.rewards.find(candidate => candidate.id === accrual.ledger_entry_id);
  const ledger = accrual.ledger_amount ?? null;
  const account = accrual.reward_account_name;
  const { state, estimate, amount } = accrual;

  let figure: string;
  let was: string | null = null;
  if (state === 'forfeited') {
    figure = unit(Number(amount) > 0 ? amount : estimate);
  } else {
    const shown = ledger ?? amount;
    figure = unit(shown, true);
    was = Number(estimate) !== Number(shown) ? unit(estimate) : null;
  }

  let line: string;
  let link: number | null = null;
  if (state === 'estimated') {
    line = Number(amount) < Number(estimate)
      ? `算出 ${unit(estimate)}，本期上限只剩 ${unit(amount)}`
      : accrual.post_on ? `預計 ${shortDate(accrual.post_on)} 入帳到 ${account}` : '手動入帳';
  } else if (state === 'posted') {
    line = postedLine(row?.posted_date ?? accrual.post_on, account, today);
    link = accrual.ledger_entry_id;
  } else if (state === 'bank_confirmed') {
    line = ledger === null || Number(ledger) === Number(amount) ? '與帳單相符' : `銀行實際給 ${unit(ledger)}`;
  } else if (accrual.ledger_entry_id !== null) {
    line = '帳單上沒有這筆回饋';
  } else {
    line = Number(amount) === 0 ? '本期上限已用完' : '回饋記錄已刪除，不會入帳';
  }
  return { key: `a${accrual.id}`, name: accrual.rule_name, state, amount: figure, was, line, link };
}

/** The entry detail's 回饋 cards (D7): one per accrual in API order, then reward rows no accrual points at. */
export function rewardCards(detail: EntryDetail, today: string, text: RewardAmountText): RewardCard[] {
  const cards = detail.accruals.map(accrual => accrualCard(detail, accrual, today, text));
  const linked = new Set(detail.accruals.map(accrual => accrual.ledger_entry_id));
  for (const reward of detail.rewards) {
    if (linked.has(reward.id)) {
      continue;
    }
    cards.push({
      key: `r${reward.id}`,
      name: reward.name || '紅利回饋',
      state: 'posted',
      amount: text(reward.amount, reward.account_id, reward.account_name, reward.currency, true),
      was: null,
      line: postedLine(reward.posted_date, reward.account_name, today),
      link: reward.id,
    });
  }
  return cards;
}
