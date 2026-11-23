import { makeItem } from '../../accounting/inbox/testing';
import { InboxItem } from '../../accounting/inbox/inbox.model';
import {
  FlowState,
  advance,
  afterRead,
  askItems,
  followers,
  goTo,
  move,
  progress,
  resume,
  skip,
  startFlow,
  steps,
} from './recon-flow-state';

function items(n: number): InboxItem[] {
  return Array.from({ length: n }, (_, i) => makeItem({ case_id: i + 1 }));
}

function without(list: InboxItem[], ...ids: number[]): InboxItem[] {
  return list.filter((item) => !ids.includes(item.case_id));
}

function pair(): InboxItem[] {
  const entry = { id: 812, date: '2026-09-03', title: '小店', amount: '-100.0000' };
  return [
    makeItem({
      case_id: 1,
      question: 'amount_differs',
      candidates: [{ entry_id: 812, date: '2026-09-03', title: '小店', amount: '-100.0000' } as never],
    }),
    makeItem({ case_id: 2, question: 'ledger_only', line: null, entry: entry as never }),
  ];
}

describe('recon-flow-state', () => {
  it('askItems keeps open ask items of one statement', () => {
    const list = [
      makeItem({ case_id: 1 }),
      makeItem({ case_id: 2, folded: true }),
      makeItem({ case_id: 3, question: 'proposed' }),
      makeItem({ case_id: 4, statement: { id: 11, period_end: '2026-09-30' } }),
    ];
    expect(askItems(list, 10).map((i) => i.case_id)).toEqual([1]);
  });

  it('startFlow of 10 items starts at the first', () => {
    const s = startFlow(items(10));
    expect(s.cursor).toBe(1);
    expect(progress(s)).toEqual({ position: 1, total: 10, handled: 0, remaining: 10 });
  });

  it('answering moves on', () => {
    const list = items(10);
    const s = advance(afterRead(startFlow(list), without(list, 1)));
    expect(s.cursor).toBe(2);
    expect(progress(s).position).toBe(2);
    expect(progress(s).handled).toBe(1);
  });

  it('hides a follower behind its leader', () => {
    const list = pair();
    const s = startFlow(list);
    expect(followers(s)).toEqual(new Map([[2, 1]]));
    expect(steps(s)).toEqual([1]);
    const both = afterRead(s, []);
    expect(progress(both).handled).toBe(2);
    const onlyFollower = afterRead(s, [list[1]]);
    expect(followers(onlyFollower).size).toBe(0);
    expect(steps(onlyFollower)).toEqual([2]);
  });

  it('puts a follower listed before its leader directly after it', () => {
    const [leader, follower] = pair();
    const third = makeItem({ case_id: 3 });
    const s = startFlow([follower, leader, third]);
    expect(s.order).toEqual([1, 2, 3]);
    expect(s.cursor).toBe(1);
    expect(progress(s).position).toBe(1);
    // 不是同一筆: only the leader leaves; the follower is now a real question and comes next.
    const next = advance(afterRead(s, [follower, third]));
    expect(next.cursor).toBe(2);
  });

  it('skip on the last step ends, resume restarts', () => {
    const s0 = startFlow(items(2));
    const s1 = skip(skip(s0));
    expect(s1.cursor).toBeNull();
    expect(progress(s1).position).toBe(0);
    const r = resume(s1);
    expect(r.cursor).toBe(1);
    expect(r.skipped.size).toBe(0);
  });

  it('move is a no-op at the ends and lands on skipped steps', () => {
    const s0 = startFlow(items(3));
    expect(move(s0, -1)).toEqual(s0);
    const s1 = skip(s0);
    expect(s1.cursor).toBe(2);
    expect(move(s1, -1).cursor).toBe(1);
    const last = goTo(s1, 3);
    expect(move(last, 1)).toEqual(last);
  });

  it('appends a case that appears later and goTo reaches it', () => {
    const list = items(2);
    const s = afterRead(startFlow(list), [...list, makeItem({ case_id: 7 })]);
    expect(s.order).toEqual([1, 2, 7]);
    expect(goTo(s, 7).cursor).toBe(7);
    expect(goTo(s, 99)).toEqual(s);
  });

  it('empty flow has no cursor', () => {
    const s = startFlow([]);
    expect(s.cursor).toBeNull();
    expect(progress(s).total).toBe(0);
  });

  it('stop midway counts handled and remaining', () => {
    const list = items(10);
    const s = afterRead(startFlow(list), without(list, 1, 2, 3, 4));
    expect(progress(s)).toMatchObject({ handled: 4, remaining: 6 });
  });

  it('does not mutate the input state', () => {
    const list = items(3);
    const s: FlowState = startFlow(list);
    afterRead(s, without(list, 1));
    skip(s);
    expect(s.open.size).toBe(3);
    expect(s.skipped.size).toBe(0);
  });
});
