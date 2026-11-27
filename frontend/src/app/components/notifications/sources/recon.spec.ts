import { describe, expect, it } from 'vitest';

import { makeEinvoice, makeInbox, makeInboxAccount, makeItem } from '../../accounting/inbox/testing';
import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { InboxCounts, InboxItem, InboxOut, InboxQuestion } from '../../accounting/inbox/inbox.model';
import { sortTasks, summarize } from '../notif-task';
import { snoozeEntry, wakes } from '../snooze';
import {
  INBOX_READ_LIMIT,
  askAccountIds,
  einvoiceTask,
  inboxMissing,
  reconOverflowTask,
  reconTaskRoute,
  reconTasks,
} from './recon';

function items(n: number, statementId: number, overrides: Partial<InboxItem> = {}): InboxItem[] {
  return Array.from({ length: n }, (_, i) =>
    makeItem({
      case_id: statementId * 1000 + i,
      statement: { id: statementId, period_end: '2026-10-05' },
      ...overrides,
    }),
  );
}

function inboxWith(accountItems: InboxItem[], counts: Partial<InboxCounts> = {}): InboxOut {
  return makeInbox([makeInboxAccount({ id: 11, name: '測試銀行卡', items: accountItems })], counts);
}

describe('reconTaskRoute', () => {
  it('points at the review flow of the statement on the notifications page', () => {
    expect(reconTaskRoute(11, 31)).toBe('/notifications/recon/31');
    expect(reconTaskRoute(3, 12)).toBe('/notifications/recon/12');
  });
});

describe('reconTasks', () => {
  it('ten ask items of one statement form one task', () => {
    const inbox = inboxWith(items(10, 31), { ask_items: 10 });
    const [t, ...rest] = reconTasks(inbox, new Map([[31, { k: 17, n: 28 }]]));
    expect(rest).toEqual([]);
    expect(t).toMatchObject({
      id: 'recon:31',
      source: 'ledger',
      from: 'recon',
      tier: 'confirm',
      icon: 'pi-check-square',
      title: '對帳 · 測試銀行卡（至 10/05）',
      when: '10 筆要你決定',
      meta: '已對上 17 / 28 行',
      amount: null,
      secondary: [],
      progress: { k: 17, n: 28 },
      primary: { label: '對帳', route: '/notifications/recon/31' },
      sortKey: '2026-10-05',
      wake: { count: 10 },
    });
  });

  it('counts items, not cards: three items sharing a group_key count 3', () => {
    const inbox = inboxWith(items(3, 31, { group_key: 'g1' }));
    expect(reconTasks(inbox, new Map())[0].when).toBe('3 筆要你決定');
  });

  it('folded items and proposed / balance_gap / settlement / dividend / other items make no task', () => {
    const others = (['proposed', 'balance_gap', 'settlement', 'dividend', 'other'] as InboxQuestion[]).flatMap(
      (question, i) => items(1, 40 + i, { question }),
    );
    const inbox = inboxWith([...items(2, 31, { folded: true }), ...others]);
    expect(reconTasks(inbox, new Map())).toEqual([]);
  });

  it('two statements of one account give two tasks, oldest period first after sortTasks', () => {
    const newer = items(2, 32).map((i) => ({ ...i, statement: { id: 32, period_end: '2026-11-05' } }));
    const older = items(1, 31);
    const tasks = reconTasks(inboxWith([...newer, ...older]), new Map());
    expect(sortTasks(tasks).map((t) => t.id)).toEqual(['recon:31', 'recon:32']);
  });

  it('without progress the task has no bar and no meta', () => {
    const [t] = reconTasks(inboxWith(items(1, 31)), new Map());
    expect(t.progress).toBeNull();
    expect(t.meta).toBeNull();
  });
});

describe('askAccountIds', () => {
  it('lists each account once, only those with an unfolded ask item', () => {
    const inbox = makeInbox([
      makeInboxAccount({ id: 11, items: [...items(2, 31), ...items(1, 32)] }),
      makeInboxAccount({ id: 12, items: items(2, 33, { folded: true }) }),
      makeInboxAccount({ id: 13, items: items(1, 34, { question: 'proposed' }) }),
      makeInboxAccount({ id: 14, items: items(1, 35) }),
    ]);
    expect(askAccountIds(inbox)).toEqual([11, 14]);
  });
});

describe('einvoiceTask', () => {
  const asking = [
    makeEinvoice({ id: 1, seller_name: '測試咖啡', issue_date: '2026-10-22' }),
    makeEinvoice({ id: 2, seller_name: '測試咖啡', issue_date: '2026-10-20' }),
    makeEinvoice({ id: 3, state: 'near', seller_name: '測試超商', issue_date: '2026-10-21' }),
  ];
  const withEinvoices = (einvoices: InboxEinvoice[], einvoice_ask: number) =>
    ({ ...inboxWith([], { einvoice_ask }), einvoices });

  it('three to decide give one task: count, up to three sellers, the newest date, the flow route, wake by count + members', () => {
    expect(einvoiceTask(withEinvoices(asking, 3))).toEqual({
      id: 'einvoice',
      source: 'ledger',
      from: 'recon',
      tier: 'confirm',
      icon: 'pi-receipt',
      title: '電子發票 · 3 張要確認',
      when: '最新 10/22',
      meta: '測試咖啡、測試超商',
      amount: null,
      progress: null,
      primary: { label: '確認', route: '/notifications/einvoices' },
      secondary: [],
      sortKey: '9999-12-31',
      wake: { count: 3, members: ['1', '2', '3'] },
    });
  });

  it('snooze: a new invoice wakes; one answered (and another moving up) does not', () => {
    const now = new Date('2026-10-24T02:00:00Z');
    const entry = snoozeEntry(einvoiceTask(withEinvoices(asking, 3))!, new Date('2026-10-26T01:00:00Z'));
    const fresh = makeEinvoice({ id: 4, seller_name: '測試書店', issue_date: '2026-10-23' });
    expect(wakes(entry, einvoiceTask(withEinvoices([...asking, fresh], 4))!, now)).toBe(true);
    expect(wakes(entry, einvoiceTask(withEinvoices(asking.slice(1), 2))!, now)).toBe(false);
    // the same count with one invoice swapped for a new one still wakes (members)
    expect(wakes(entry, einvoiceTask(withEinvoices([asking[0], asking[1], fresh], 3))!, now)).toBe(true);
  });

  it('snooze saved before members (count only) still wakes when invoices are added', () => {
    const now = new Date('2026-10-24T02:00:00Z');
    const legacy = { until: '2026-10-26T01:00:00.000Z', tier: 'confirm' as const, from: 'recon' as const, wake: { count: 3 } };
    const fresh = makeEinvoice({ id: 4, seller_name: '測試書店' });
    expect(wakes(legacy, einvoiceTask(withEinvoices([...asking, fresh], 4))!, now)).toBe(true);
    expect(wakes(legacy, einvoiceTask(withEinvoices(asking, 3))!, now)).toBe(false);
  });

  it('a cut page (fewer listed than the count) wakes by count only: an invoice moving into the page is not new', () => {
    const now = new Date('2026-10-24T02:00:00Z');
    const entry = snoozeEntry(einvoiceTask(withEinvoices(asking.slice(0, 2), 5))!, new Date('2026-10-26T01:00:00Z'));
    expect(entry.wake).toEqual({ count: 5 });
    // invoice 1 answered: 3 moves into the page, the count drops to 4 → no wake
    expect(wakes(entry, einvoiceTask(withEinvoices([asking[1], asking[2]], 4))!, now)).toBe(false);
    expect(wakes(entry, einvoiceTask(withEinvoices(asking.slice(0, 2), 6))!, now)).toBe(true);
  });

  it('counts 1 in the notification count (3 invoices + 6 other counted tasks → 7)', () => {
    const others = Array.from({ length: 6 }, (_, i) => ({ ...einvoiceTask(withEinvoices(asking, 3))!, id: `x${i}` }));
    expect(summarize([einvoiceTask(withEinvoices(asking, 3))!, ...others]).total).toBe(7);
  });

  it('names the first three distinct sellers only', () => {
    const four = ['測試甲', '測試乙', '測試丙', '測試丁'].map((seller_name, i) => makeEinvoice({ id: i + 1, seller_name }));
    expect(einvoiceTask(withEinvoices(four, 4))!.meta).toBe('測試甲、測試乙、測試丙');
  });

  it('the count is the whole set (counts.einvoice_ask), not the listed page; unmatched invoices add nothing', () => {
    const listed = [asking[0], makeEinvoice({ id: 9, state: 'unmatched', seller_name: '測試書店', issue_date: '2026-10-30' })];
    const t = einvoiceTask(withEinvoices(listed, 5))!;
    expect(t.title).toBe('電子發票 · 5 張要確認');
    expect(t.meta).toBe('測試咖啡');
    expect(t.when).toBe('最新 10/22');
    expect(t.wake).toEqual({ count: 5 }); // cut page: count only
  });

  it('none listed (an older API or a truncated page) still gives the task, with no meta and no date', () => {
    const t = einvoiceTask(inboxWith([], { einvoice_ask: 2 }))!;
    expect(t).toMatchObject({ title: '電子發票 · 2 張要確認', meta: null, when: '' });
    expect(t.wake).toEqual({ count: 2 });
  });

  it('0 or absent gives null, even with unmatched invoices listed', () => {
    expect(einvoiceTask(withEinvoices([makeEinvoice({ state: 'unmatched' })], 0))).toBeNull();
    expect(einvoiceTask(inboxWith([]))).toBeNull();
  });
});

describe('inboxMissing', () => {
  it('every unfolded item received gives 0', () => {
    const inbox = inboxWith([...items(10, 31), ...items(4, 32, { folded: true })], { ask_items: 10 });
    expect(inboxMissing(inbox)).toBe(0);
  });

  it('a statement cut in two keeps its row with what was received; the rest is missing', () => {
    const inbox = inboxWith(items(6, 31), { ask_items: 10 });
    expect(reconTasks(inbox, new Map())[0].when).toBe('6 筆要你決定');
    expect(inboxMissing(inbox)).toBe(4);
  });

  it('a statement wholly beyond the cut, only folded items received: no statement task, missing 7', () => {
    const inbox = inboxWith(items(INBOX_READ_LIMIT, 31, { folded: true }), { ask_items: 7 });
    expect(reconTasks(inbox, new Map())).toEqual([]);
    expect(inboxMissing(inbox)).toBe(7);
  });

  it('never goes below 0 (an older API without ask_items gives 0)', () => {
    const inbox = inboxWith(items(3, 31));
    delete (inbox.counts as Partial<InboxCounts>).ask_items;
    expect(inboxMissing(inbox)).toBe(0);
    expect(inboxMissing(inboxWith(items(3, 31), { ask_items: 1 }))).toBe(0);
  });
});

describe('inboxMissing without counts', () => {
  const noTotal = (list: InboxItem[]): InboxOut => {
    const inbox = inboxWith(list);
    delete (inbox as Partial<InboxOut>).counts;
    return inbox;
  };

  it('absent counts and a short response: not truncated', () => {
    expect(inboxMissing(noTotal(items(3, 31)))).toBe(0);
  });

  it('absent counts and a full response: truncated, count unknown', () => {
    const full = noTotal(items(INBOX_READ_LIMIT, 31, { folded: true }));
    expect(inboxMissing(full)).toBe('unknown');
    expect(inboxMissing(full, INBOX_READ_LIMIT + 1)).toBe(0);
  });

  it('absent ask_items and a full response: truncated, count unknown', () => {
    const inbox = inboxWith(items(5, 31));
    delete (inbox.counts as Partial<InboxCounts>).ask_items;
    expect(inboxMissing(inbox, 5)).toBe('unknown');
  });

  it('a statement exactly at the limit with nothing missing gives 0 and no catch-all', () => {
    const inbox = inboxWith(items(INBOX_READ_LIMIT, 31), { ask_items: INBOX_READ_LIMIT });
    expect(inboxMissing(inbox)).toBe(0);
    expect(reconOverflowTask(inboxMissing(inbox))).toBeNull();
  });

  it('the unknown catch-all has no number', () => {
    expect(reconOverflowTask('unknown')).toMatchObject({
      id: 'recon:more',
      title: '還有對帳項目未列出',
      when: '',
      wake: { key: 'more' },
    });
  });
});

describe('reconTasks duplicate statement id', () => {
  it('a statement id under two accounts gives one task with the summed count', () => {
    const inbox = makeInbox([
      makeInboxAccount({ id: 11, name: '測試銀行卡', items: items(2, 31) }),
      makeInboxAccount({ id: 12, name: '測試二卡', items: items(3, 31) }),
    ]);
    const tasks = reconTasks(inbox, new Map());
    expect(tasks).toHaveLength(1);
    expect(tasks[0]).toMatchObject({ id: 'recon:31', when: '5 筆要你決定' });
  });
});

describe('reconOverflowTask', () => {
  it('4 gives the catch-all task', () => {
    expect(reconOverflowTask(4)).toMatchObject({
      id: 'recon:more',
      source: 'ledger',
      from: 'recon',
      tier: 'confirm',
      icon: 'pi-list',
      title: '還有 4 筆對帳項目未列出',
      when: '',
      meta: '到提醒中心的待處理查看',
      amount: null,
      progress: null,
      primary: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'todo' } },
      secondary: [],
      sortKey: '9999-12-30',
      wake: { count: 4 },
    });
  });

  it('0 gives null', () => {
    expect(reconOverflowTask(0)).toBeNull();
  });

  it('a truncated inbox counts one per statement, whatever is cut: the catch-all is not among the counted tasks', () => {
    const inbox = inboxWith([...items(6, 31), ...items(3, 32)], { ask_items: 15 });
    const tasks = reconTasks(inbox, new Map());
    expect(tasks.map((t) => t.id)).toEqual(['recon:31', 'recon:32']);
    expect(summarize(tasks).total).toBe(2);
    expect(inboxMissing(inbox)).toBe(6);
    expect(reconOverflowTask(inboxMissing(inbox))).toMatchObject({ id: 'recon:more', title: '還有 6 筆對帳項目未列出' });
    expect(tasks.some((t) => t.id === 'recon:more')).toBe(false);
  });
});
