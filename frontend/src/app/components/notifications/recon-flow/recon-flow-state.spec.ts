import { makeItem as baseItem } from '../../accounting/inbox/testing';
import { InboxCandidate, InboxEntry, InboxItem } from '../../accounting/inbox/inbox.model';
import {
  FlowState,
  advance,
  afterRead,
  askItems,
  followers,
  goTo,
  itemKey,
  move,
  progress,
  restore,
  resume,
  skip,
  startFlow,
  steps,
} from './recon-flow-state';

/** `makeItem` with a fictional merchant (its default is a real store name). */
function makeItem(over: Partial<InboxItem> = {}): InboxItem {
  return baseItem({
    line: { id: 5, date: '2026-09-03', merchant: '測試小店', amount: '-100.0000', line_kind: 'purchase' },
    rule_hint: null,
    ...over,
  });
}

const ENTRY: InboxEntry = { id: 812, date: '2026-09-03', title: '測試小店', amount: '-100.0000', account_id: 1 };
const CANDIDATE: InboxCandidate = {
  entry_id: 812,
  date: '2026-09-03',
  title: '測試小店',
  amount: '-100.0000',
  reason: null,
  residual: null,
  date_distance: 0,
};

function leaderItem(caseId: number): InboxItem {
  return makeItem({ case_id: caseId, question: 'amount_differs', candidates: [CANDIDATE] });
}

function followerItem(caseId: number): InboxItem {
  return makeItem({ case_id: caseId, question: 'ledger_only', line: null, entry: ENTRY });
}

function items(n: number): InboxItem[] {
  return Array.from({ length: n }, (_, i) => makeItem({ case_id: i + 1 }));
}

function without(list: InboxItem[], ...ids: number[]): InboxItem[] {
  return list.filter((item) => !ids.includes(item.case_id));
}

function pair(): InboxItem[] {
  return [leaderItem(1), followerItem(2)];
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

  it('advance from a mid-list cursor whose case left goes to the next after its place, not back', () => {
    const list = items(5);
    const at3 = goTo(startFlow(list), 3);
    const next = advance(afterRead(at3, without(list, 3)));
    expect(next.cursor).toBe(4);
    // The last one leaving with earlier steps still open and not skipped: wrap to the first of them, not the end.
    const atLast = goTo(startFlow(list), 5);
    expect(advance(afterRead(atLast, without(list, 5))).cursor).toBe(1);
    // Skipped earlier steps are not wrapped to: then it is the end state.
    const skippedFirst = goTo(skip(skip(startFlow(items(3)))), 3);
    expect(advance(afterRead(skippedFirst, without(items(3), 3))).cursor).toBeNull();
  });

  it('skipping to the end with items left: no cursor, remaining > 0', () => {
    const s = skip(skip(skip(startFlow(items(3)))));
    expect(s.cursor).toBeNull();
    expect(progress(s)).toMatchObject({ remaining: 3, handled: 0, position: 0 });
  });

  it('resume with a follower present starts at the leader and keeps the follower hidden', () => {
    const s = skip(skip(startFlow([...pair(), makeItem({ case_id: 3 })])));
    expect(s.cursor).toBeNull();
    const r = resume(s);
    expect(r.cursor).toBe(1);
    expect(steps(r)).toEqual([1, 3]);
  });

  it('advance passes over a hidden follower', () => {
    const s = startFlow([...pair(), makeItem({ case_id: 3 })]);
    expect(s.order).toEqual([1, 2, 3]);
    expect(advance(s).cursor).toBe(3);
  });

  it('move from a closed cursor goes to its neighbours', () => {
    const list = items(3);
    const closed = afterRead(goTo(startFlow(list), 2), without(list, 2));
    expect(closed.cursor).toBe(2);
    expect(move(closed, -1).cursor).toBe(1);
    expect(move(closed, 1).cursor).toBe(3);
  });

  it('two followers of one leader both sit directly after it, in their original order', () => {
    const s = startFlow([followerItem(5), makeItem({ case_id: 3 }), leaderItem(1), followerItem(4)]);
    expect(s.order).toEqual([3, 1, 5, 4]);
    expect(steps(s)).toEqual([3, 1]);
  });

  describe('restore after 復原 (the question returns under a new case id)', () => {
    const linesOf = (n: number) =>
      Array.from({ length: n }, (_, i) =>
        makeItem({ case_id: i + 1, line: { id: 100 + i + 1, date: '2026-09-03', merchant: `測試小店 ${i + 1}`, amount: '-100.0000', line_kind: 'purchase' } }),
      );

    it('itemKey: the bank line, else the ledger entry, else null', () => {
      expect(itemKey(linesOf(1)[0])).toBe('line:101');
      expect(itemKey(followerItem(2))).toBe('entry:812');
      expect(itemKey(makeItem({ line: null, entry: null }))).toBeNull();
    });

    it('a plain answer comes back at its old place with the cursor on it; numbers as before the answer', () => {
      const list = linesOf(3);
      const answered = advance(afterRead(startFlow(list), without(list, 1)));
      expect(answered.cursor).toBe(2);
      const fresh = { ...list[0], case_id: 11, case_version: 1 };
      const read = afterRead(answered, [fresh, list[1], list[2]]);
      const s = restore(read, { position: 0, keys: new Map([[1, 'line:101']]) });
      expect(s.order).toEqual([11, 2, 3]);
      expect(s.cursor).toBe(11);
      expect(progress(s)).toEqual({ position: 1, total: 3, handled: 0, remaining: 3 });
    });

    it('a pair comes back leader first even when the server lists the follower first', () => {
      const list = [leaderItem(1), followerItem(2), linesOf(3)[2]];
      const answered = advance(afterRead(startFlow(list), [list[2]]));
      expect(answered.cursor).toBe(3);
      const leader = { ...leaderItem(21), line: list[0].line };
      const read = afterRead(answered, [followerItem(22), leader, list[2]]);
      const s = restore(read, { position: 0, keys: new Map([[1, itemKey(list[0])!], [2, 'entry:812']]) });
      expect(s.order).toEqual([21, 22, 3]);
      expect(s.cursor).toBe(21);
      expect(steps(s)).toEqual([21, 3]);
      expect(progress(s)).toMatchObject({ total: 3, handled: 0 });
    });

    it('from the end state the returned question becomes the cursor', () => {
      const list = linesOf(2);
      const done = advance(afterRead(advance(afterRead(startFlow(list), [list[1]])), []));
      expect(done.cursor).toBeNull();
      const s = restore(afterRead(done, [{ ...list[1], case_id: 12, case_version: 1 }]), {
        position: 1,
        keys: new Map([[2, 'line:102']]),
      });
      expect(s.order).toEqual([1, 12]);
      expect(s.cursor).toBe(12);
    });

    it('the old id itself back open (no reconcile) is moved to the cursor in place', () => {
      const list = linesOf(3);
      const answered = advance(afterRead(startFlow(list), without(list, 1)));
      const s = restore(afterRead(answered, list), { position: 0, keys: new Map([[1, 'line:101']]) });
      expect(s.order).toEqual([1, 2, 3]);
      expect(s.cursor).toBe(1);
    });

    it('nothing came back: the state is returned unchanged', () => {
      const list = linesOf(3);
      const read = afterRead(advance(afterRead(startFlow(list), without(list, 1))), without(list, 1));
      expect(restore(read, { position: 0, keys: new Map([[1, 'line:101']]) })).toBe(read);
    });

    it('a follower that did not come back stays counted as handled', () => {
      const list = [leaderItem(1), followerItem(2), linesOf(3)[2]];
      const answered = advance(afterRead(startFlow(list), [list[2]]));
      // Reconcile failed: the revert reopened the leader under its old id; the follower stays closed.
      const s = restore(afterRead(answered, [list[0], list[2]]), {
        position: 0,
        keys: new Map([[1, itemKey(list[0])!], [2, 'entry:812']]),
      });
      expect(s.order).toEqual([1, 2, 3]);
      expect(s.cursor).toBe(1);
      expect(progress(s)).toMatchObject({ total: 3, handled: 1 });
    });

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
