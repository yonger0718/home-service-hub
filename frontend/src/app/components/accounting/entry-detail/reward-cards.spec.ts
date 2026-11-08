import { describe, expect, it } from 'vitest';

import { EntryDetail, RewardAccrual } from '../../../models/accounting.model';
import { RewardAmountText, rewardCards } from './reward-cards';

const text: RewardAmountText = (v, id, name, _c, sign) =>
  id === 99
    ? `${sign && Number(v) > 0 ? '+' : ''}${Number(v)} ${name}`
    : `${sign && Number(v) > 0 ? '+' : ''}$${Number(v)}`;
const today = '2026-11-21';

function accrual(fields: Record<string, unknown> = {}): RewardAccrual {
  return {
    id: 1, rule_id: 1, rule_name: '一般消費 1%', estimate: '13', amount: '13', state: 'estimated',
    post_on: '2026-12-15', ledger_entry_id: null, ledger_amount: null, reward_account_id: 7,
    reward_account_name: '玉山 Pi 卡', ...fields,
  } as RewardAccrual;
}

function reward(fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 501, name: null, amount: '13', account_id: 7, account_name: '玉山 Pi 卡', currency: 'TWD',
    posted_date: '2026-11-15', ...fields,
  };
}

function detail(accruals: RewardAccrual[], rewards: Record<string, unknown>[] = []): EntryDetail {
  return { currency: 'TWD', accruals, rewards } as unknown as EntryDetail;
}

function one(a: RewardAccrual, rewards: Record<string, unknown>[] = []) {
  const cards = rewardCards(detail([a], rewards), today, text);
  expect(cards).toHaveLength(1);
  return cards[0];
}

describe('rewardCards', () => {
  it('estimated: expected date and account', () => {
    expect(one(accrual())).toMatchObject({
      state: 'estimated', amount: '+$13', was: null, line: '預計 12/15 入帳到 玉山 Pi 卡', link: null,
    });
  });

  it('estimated without post_on reads 手動入帳', () => {
    expect(one(accrual({ post_on: null })).line).toBe('手動入帳');
  });

  it('estimated cut by the cap', () => {
    expect(one(accrual({ estimate: '64', amount: '20' }))).toMatchObject({
      amount: '+$20', was: '$64', line: '算出 $64，本期上限只剩 $20',
    });
  });

  it('no strike when the estimate equals the figure numerically', () => {
    expect(one(accrual({ estimate: '13.00', amount: '13' })).was).toBeNull();
  });

  it('posted: row date, account and link', () => {
    const card = one(accrual({ state: 'posted', ledger_entry_id: 501, ledger_amount: '13' }), [reward()]);
    expect(card).toMatchObject({ line: '11/15 已入帳到 玉山 Pi 卡', link: 501 });
  });

  it('posted whose row is missing from rewards reads 已入帳 without a date', () => {
    const card = one(accrual({ state: 'posted', post_on: null, ledger_entry_id: 999, ledger_amount: '13' }));
    expect(card.line).toBe('已入帳到 玉山 Pi 卡');
  });

  it('posted to a future date reads 將於', () => {
    const card = one(accrual({ state: 'posted', ledger_entry_id: 501, ledger_amount: '13' }),
      [reward({ posted_date: '2026-12-15' })]);
    expect(card.line).toBe('將於 12/15 入帳到 玉山 Pi 卡');
  });

  it('bank_confirmed matching', () => {
    const card = one(accrual({ state: 'bank_confirmed', ledger_entry_id: 501, ledger_amount: '13' }), [reward()]);
    expect(card).toMatchObject({ line: '與帳單相符', was: null, link: 501 });
  });

  it('bank_confirmed with another figure', () => {
    const card = one(accrual({ state: 'bank_confirmed', ledger_entry_id: 501, ledger_amount: '12' }),
      [reward({ amount: '12' })]);
    expect(card).toMatchObject({ amount: '+$12', was: '$13', line: '銀行實際給 $12', link: 501 });
  });

  it('bank_confirmed from an older API without ledger_amount', () => {
    const a = accrual({ state: 'bank_confirmed', ledger_entry_id: 501 });
    delete (a as unknown as Record<string, unknown>)['ledger_amount'];
    expect(one(a, [reward()])).toMatchObject({ amount: '+$13', line: '與帳單相符', link: 501 });
  });

  it('forfeited by the statement', () => {
    const card = one(accrual({ state: 'forfeited', amount: '10', estimate: '10', ledger_entry_id: 502, ledger_amount: '0' }));
    expect(card).toMatchObject({ amount: '$10', was: null, line: '帳單上沒有這筆回饋', link: 502 });
  });

  it('forfeited by the statement prints the capped amount, not the estimate', () => {
    const card = one(accrual({ state: 'forfeited', estimate: '64', amount: '20', ledger_entry_id: 503, ledger_amount: '0' }));
    expect(card).toMatchObject({ amount: '$20', was: null, line: '帳單上沒有這筆回饋', link: 503 });
  });

  it('forfeited past the cap', () => {
    const card = one(accrual({ state: 'forfeited', estimate: '8', amount: '0' }));
    expect(card).toMatchObject({ amount: '$8', line: '本期上限已用完', link: null });
  });

  it('forfeited after the row was deleted', () => {
    const card = one(accrual({ state: 'forfeited', amount: '13' }));
    expect(card).toMatchObject({ amount: '$13', line: '回饋記錄已刪除，不會入帳', link: null });
  });

  it('points in every figure', () => {
    const card = one(accrual({ reward_account_id: 99, reward_account_name: '亞洲萬里通', estimate: '150', amount: '120' }));
    expect(card).toMatchObject({
      amount: '+120 亞洲萬里通', was: '150 亞洲萬里通', line: '算出 150 亞洲萬里通，本期上限只剩 120 亞洲萬里通',
    });
  });

  it('imported reward row without an accrual', () => {
    const cards = rewardCards(detail([], [reward({ id: 600, amount: '30', posted_date: '2026-10-15' })]), today, text);
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({
      key: 'r600', name: '紅利回饋', state: 'posted', amount: '+$30', was: null, line: '10/15 已入帳到 玉山 Pi 卡', link: 600,
    });
  });

  it('accrual cards keep API order and come before unlinked rows', () => {
    const cards = rewardCards(detail(
      [accrual({ id: 9, rule_name: 'B' }), accrual({ id: 3, rule_name: 'A', state: 'posted', ledger_entry_id: 501, ledger_amount: '13' })],
      [reward({ id: 700 }), reward({ id: 501 })],
    ), today, text);
    expect(cards.map(card => card.key)).toEqual(['a9', 'a3', 'r700']);
  });
});
