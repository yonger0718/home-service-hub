import { describe, expect, it } from 'vitest';

import { makeEinvoice } from '../../accounting/inbox/testing';
import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { itemsText, unmatchedGroups, unmatchedHeadline } from './einvoice-unmatched-view';

function inv(id: number, overrides: Partial<InboxEinvoice> = {}): InboxEinvoice {
  return makeEinvoice({ id, number: `AB0000${String(id).padStart(4, '0')}`, state: 'unmatched', ...overrides });
}

const BOOKS = { seller_name: '測試書店', seller_tax_id: '00000011' };
const MART = { seller_name: '測試超商', seller_tax_id: '00000022' };
const NO_TAX = { seller_name: '測試小吃', seller_tax_id: '' };

describe('itemsText', () => {
  it('first two names and the count', () => {
    const items = [{ name: '筆記本', subtotal: '1' }, { name: '原子筆', subtotal: '1' }, { name: '膠帶', subtotal: '1' }];
    expect(itemsText(inv(1, { items }))).toBe('筆記本、原子筆 · 3 項');
    expect(itemsText(inv(1, { items: items.slice(0, 1) }))).toBe('筆記本 · 1 項');
    expect(itemsText(inv(1, { items: [] }))).toBe('');
  });
});

describe('unmatchedGroups', () => {
  it('groups by seller, by count then total; rows newest first; meta with tax id and earlier dismissals', () => {
    const groups = unmatchedGroups([
      inv(1, { ...MART, issue_date: '2026-10-09', total_amount: '120' }),
      inv(2, { ...BOOKS, issue_date: '2026-10-03', total_amount: '740', seller_dismissed: 3, offer_mute: true }),
      inv(3, { ...BOOKS, issue_date: '2026-10-25', total_amount: '680', seller_dismissed: 3, offer_mute: true }),
      inv(4, { ...MART, issue_date: '2026-10-27', total_amount: '312', seller_dismissed: 1 }),
      inv(5, { ...NO_TAX, issue_date: '2026-10-30', total_amount: '159' }),
      inv(6, { state: 'near', seller_name: '測試問題' }),
    ]);
    expect(groups.map((g) => g.seller)).toEqual(['測試書店', '測試超商', '測試小吃']);
    expect(groups[0]).toMatchObject({
      meta: '2 張 · 統編 00000011 · 已標不用記帳 3 次',
      total: '$1,420',
      canMute: true,
      taxId: '00000011',
    });
    expect(groups[0].rows.map((r) => [r.date, r.amount])).toEqual([['10/25', '$680'], ['10/03', '$740']]);
    expect(groups[1]).toMatchObject({ meta: '2 張 · 統編 00000022 · 已標不用記帳 1 次', canMute: false });
    expect(groups[2]).toMatchObject({ meta: '1 張 · 沒有統編', taxId: null, canMute: false });
  });

  it('equal counts: the larger total first', () => {
    const groups = unmatchedGroups([inv(1, { ...BOOKS, total_amount: '50' }), inv(2, { ...MART, total_amount: '300' })]);
    expect(groups.map((g) => g.seller)).toEqual(['測試超商', '測試書店']);
  });

  it('no tax id never offers the mute, even with offer_mute', () => {
    expect(unmatchedGroups([inv(1, { ...NO_TAX, offer_mute: true })])[0].canMute).toBe(false);
  });

  it('headline counts what is shown', () => {
    const groups = unmatchedGroups([inv(1, BOOKS), inv(2, BOOKS), inv(3, MART)]);
    expect(unmatchedHeadline(groups)).toBe('3 張 · 2 家 · 不計入通知數字');
  });
});
