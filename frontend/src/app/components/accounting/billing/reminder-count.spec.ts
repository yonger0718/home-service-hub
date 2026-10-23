import { describe, expect, it } from 'vitest';

import { Counterparty } from '../../../models/accounting.model';
import { makeInstance } from '../testing/fixtures';
import { BillingEvent } from './billing-math';
import { BillState } from './billing.service';
import { countReminders } from './reminder-count';

const TODAY = '2026-10-22';
const TOMORROW = '2026-10-23';

function due(accountId: number): BillingEvent {
  return {
    accountId,
    name: `卡 ${accountId}`,
    kind: 'due',
    date: '2026-11-05',
    period: { start: '2026-09-16', end: '2026-10-15' },
    nextClosing: '2026-11-15',
  };
}

function billOf(event: BillingEvent, remaining: number): BillState {
  return {
    accountId: event.accountId,
    name: event.name,
    period: event.period,
    due: event.date,
    currency: 'TWD',
    statement: remaining,
    paid: 0,
    remaining,
  };
}

function counterparty(id: number, openCount: number): Counterparty {
  return { id, name: `對象 ${id}`, open_amounts: [], open_count: openCount };
}

describe('countReminders', () => {
  it('counts 1 open bill + 1 counterparty + 2 due queue items = 4', () => {
    const owing = due(1);
    const paid = due(2);
    const count = countReminders({
      dues: [owing, paid],
      bill: event => billOf(event, event.accountId === 1 ? 500 : 0),
      counterparties: [counterparty(1, 2), counterparty(2, 0)],
      queue: [
        makeInstance({ id: 1, is_partial: true, due_date: TOMORROW }),
        makeInstance({ id: 2, is_partial: false, due_date: TODAY }),
        makeInstance({ id: 3, is_partial: false, due_date: TOMORROW }),
      ],
      today: TODAY,
    });
    expect(count).toBe(4);
  });

  it('unresolved bill (null) is not counted', () => {
    const count = countReminders({ dues: [due(1)], bill: () => null, counterparties: [], queue: [], today: TODAY });
    expect(count).toBe(0);
  });

  it('adds the inbox ask count', () => {
    const count = countReminders({ dues: [], bill: () => null, counterparties: [counterparty(1, 1)], queue: [], today: TODAY, inboxAsk: 3 });
    expect(count).toBe(4);
  });
});
