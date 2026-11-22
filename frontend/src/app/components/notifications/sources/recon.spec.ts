import { describe, expect, it } from 'vitest';

import { makeInbox, makeInboxAccount, makeItem } from '../../accounting/inbox/testing';
import { InboxCounts, InboxItem, InboxOut, InboxQuestion } from '../../accounting/inbox/inbox.model';
import { sortTasks, summarize } from '../notif-task';
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
  it('points at the statement page', () => {
    expect(reconTaskRoute(11, 31)).toBe('/accounting/accounts/11/statements/31');
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
      primary: { label: '對帳', route: '/accounting/accounts/11/statements/31' },
      sortKey: '2026-10-05',
      fingerprint: '10',
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
  it('3 gives the task linking to the reminders 待處理 tab', () => {
    expect(einvoiceTask({ einvoice_ask: 3 })).toMatchObject({
      id: 'einvoice',
      source: 'ledger',
      from: 'recon',
      tier: 'confirm',
      icon: 'pi-receipt',
      title: '3 張發票要確認',
      when: '電子發票',
      meta: '到提醒中心回答',
      amount: null,
      progress: null,
      primary: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'todo' } },
      secondary: [],
      sortKey: '9999-12-31',
      fingerprint: '3',
    });
  });

  it('comes from counts.einvoice_ask alone: a truncated inbox (6 of 10 ask items listed) still gives 2, and the item list adds none', () => {
    const inbox = inboxWith(items(6, 31), { ask_items: 10, einvoice_ask: 2 });
    expect(inboxMissing(inbox)).toBe(4);
    expect(einvoiceTask(inbox.counts)).toMatchObject({ id: 'einvoice', title: '2 張發票要確認', fingerprint: '2' });
    expect(reconTasks(inbox, new Map()).map((t) => t.id)).toEqual(['recon:31']);
  });

  it('0 or absent gives null', () => {
    expect(einvoiceTask({ einvoice_ask: 0 })).toBeNull();
    expect(einvoiceTask({})).toBeNull();
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
      fingerprint: 'more',
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
      fingerprint: '4',
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
