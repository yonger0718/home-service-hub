import { describe, expect, it } from 'vitest';

import { makeInbox, makeInboxAccount, makeItem } from '../../accounting/inbox/testing';
import { InboxCounts, InboxItem, InboxOut, InboxQuestion } from '../../accounting/inbox/inbox.model';
import { sortTasks } from '../notif-task';
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
      meta: '到提醒中心的待處理回答',
      amount: null,
      progress: null,
      primary: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'todo' } },
      secondary: [],
      sortKey: '9999-12-31',
      fingerprint: '3',
    });
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

describe('reconOverflowTask', () => {
  it('4 gives the catch-all task', () => {
    expect(reconOverflowTask(4)).toMatchObject({
      id: 'recon:more',
      source: 'ledger',
      from: 'recon',
      tier: 'confirm',
      icon: 'pi-list',
      title: '還有對帳項目未列出',
      when: '還有 4 筆要你決定',
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

  it('sortTasks puts recon:more after the statements and before einvoice', () => {
    const tasks = [
      einvoiceTask({ einvoice_ask: 1 })!,
      reconOverflowTask(2)!,
      ...reconTasks(inboxWith(items(1, 31)), new Map()),
    ];
    expect(sortTasks(tasks).map((t) => t.id)).toEqual(['recon:31', 'recon:more', 'einvoice']);
  });
});
