import { NotifTask, NotifWake } from './notif-task';
import { SNOOZE_OPTIONS, SnoozeEntry, optionHint, parseSnoozes, snoozeChoices, snoozeEntry, snoozeUntil, untilText, wakes } from './snooze';

/** Local time (the device clock), so the tests hold in any TZ. */
const at = (y: number, mo: number, d: number, h = 10, mi = 0) => new Date(y, mo - 1, d, h, mi);
const SAT = at(2026, 10, 24); // Saturday 10:00

function task(over: Partial<NotifTask> = {}): NotifTask {
  return {
    id: 'sched:7',
    source: 'ledger',
    from: 'schedules',
    tier: 'today',
    icon: '🔁',
    title: '測試月費',
    when: '今天',
    meta: null,
    amount: null,
    progress: null,
    primary: { label: '入帳', route: '/x' },
    secondary: [],
    sortKey: '2026-10-24',
    wake: { count: 3 },
    ...over,
  };
}

const entry = (wake: NotifWake, over: Partial<SnoozeEntry> = {}): SnoozeEntry => ({
  until: at(2026, 10, 25, 9).toISOString(),
  tier: 'today',
  from: 'schedules',
  wake,
  ...over,
});

describe('snooze options', () => {
  it('labels are 3 小時後, 明天 09:00, 下週一 09:00', () => {
    expect(SNOOZE_OPTIONS.map((o) => o.label)).toEqual(['3 小時後', '明天 09:00', '下週一 09:00']);
  });

  it('3 小時後 is now + 3 h', () => {
    expect(snoozeUntil('3h', SAT)).toEqual(at(2026, 10, 24, 13));
  });

  it('明天 09:00 is the next calendar day, also late at night', () => {
    expect(snoozeUntil('tomorrow', SAT)).toEqual(at(2026, 10, 25, 9));
    expect(snoozeUntil('tomorrow', at(2026, 10, 24, 23, 30))).toEqual(at(2026, 10, 25, 9));
    expect(snoozeUntil('tomorrow', at(2026, 10, 31, 12))).toEqual(at(2026, 11, 1, 9));
  });

  it('下週一 09:00 is the first Monday after today: from Saturday, Sunday and Monday', () => {
    expect(snoozeUntil('monday', SAT)).toEqual(at(2026, 10, 26, 9));
    expect(snoozeUntil('monday', at(2026, 10, 25, 20))).toEqual(at(2026, 10, 26, 9));
    expect(snoozeUntil('monday', at(2026, 10, 26, 8))).toEqual(at(2026, 11, 2, 9));
  });

  it('option hints: the date and weekday; the time only for 3 小時後 (owner design review 2026-11-26)', () => {
    expect(optionHint('3h', at(2026, 10, 24, 13))).toBe('10/24（六）13:00');
    expect(optionHint('tomorrow', at(2026, 10, 25, 9))).toBe('10/25（日）');
    expect(optionHint('monday', at(2026, 10, 26, 9))).toBe('10/26（一）');
  });

  it('snoozeChoices: three options with their end and hint; on a Sunday 下週一 09:00 is 明天 09:00, so only 明天 09:00 shows', () => {
    expect(snoozeChoices(SAT)).toEqual([
      { value: '3h', label: '3 小時後', until: at(2026, 10, 24, 13), hint: '10/24（六）13:00' },
      { value: 'tomorrow', label: '明天 09:00', until: at(2026, 10, 25, 9), hint: '10/25（日）' },
      { value: 'monday', label: '下週一 09:00', until: at(2026, 10, 26, 9), hint: '10/26（一）' },
    ]);
    const sun = at(2026, 10, 25, 10);
    expect(snoozeChoices(sun).map((c) => [c.value, c.hint])).toEqual([
      ['3h', '10/25（日）13:00'],
      ['tomorrow', '10/26（一）'],
    ]);
  });

  it('untilText is MM/DD HH:mm in local time', () => {
    expect(untilText(at(2026, 10, 24, 13))).toBe('10/24 13:00');
    expect(untilText(at(2026, 11, 2, 9, 5))).toBe('11/02 09:05');
  });
});

describe('wakes', () => {
  const now = SAT;

  it('stays asleep with the same values; wakes once the time has passed', () => {
    expect(wakes(entry({ count: 3 }), task(), now)).toBe(false);
    expect(wakes(entry({ count: 3 }), task(), at(2026, 10, 25, 9))).toBe(true);
  });

  it('count: more wakes, fewer does not (owner ruling)', () => {
    expect(wakes(entry({ count: 3 }), task({ wake: { count: 4 } }), now)).toBe(true);
    expect(wakes(entry({ count: 3 }), task({ wake: { count: 2 } }), now)).toBe(false);
  });

  it('amount or due change wakes, either way', () => {
    const bill = (w: NotifWake) => task({ id: 'bill:1:2026-10-24', from: 'bills', wake: w });
    expect(wakes(entry({ amount: '1000', due: '2026-10-24' }, { from: 'bills' }), bill({ amount: '900', due: '2026-10-24' }), now)).toBe(true);
    expect(wakes(entry({ amount: '1000', due: '2026-10-24' }, { from: 'bills' }), bill({ amount: '1000', due: '2026-10-27' }), now)).toBe(true);
    expect(wakes(entry({ amount: '1000', due: '2026-10-24' }, { from: 'bills' }), bill({ amount: '1000', due: '2026-10-24' }), now)).toBe(false);
  });

  it('members: a new id wakes, a removed one does not', () => {
    const div = (m: string[]) => task({ id: 'div', from: 'dividends', tier: 'confirm', wake: { members: m } });
    const e = entry({ members: ['1', '2'] }, { from: 'dividends', tier: 'confirm' });
    expect(wakes(e, div(['1', '2', '3']), now)).toBe(true);
    expect(wakes(e, div(['2', '3']), now)).toBe(true);
    expect(wakes(e, div(['1']), now)).toBe(false);
  });

  it('key change wakes', () => {
    expect(wakes(entry({ key: 'held|a' }), task({ wake: { key: 'mismatch|a' } }), now)).toBe(true);
    expect(wakes(entry({ key: 'held|a' }), task({ wake: { key: 'held|a' } }), now)).toBe(false);
  });

  it('becoming 已逾期 wakes (owner ruling); already overdue or another tier does not', () => {
    expect(wakes(entry({ count: 3 }), task({ tier: 'overdue' }), now)).toBe(true);
    expect(wakes(entry({ count: 3 }, { tier: 'overdue' }), task({ tier: 'overdue' }), now)).toBe(false);
    expect(wakes(entry({ count: 3 }, { tier: 'confirm' }), task({ tier: 'confirm' }), now)).toBe(false);
  });

  it('a field absent on either side never wakes', () => {
    expect(wakes(entry({}), task({ wake: { count: 9 } }), now)).toBe(false);
    expect(wakes(entry({ count: 3 }), task({ wake: {} }), now)).toBe(false);
  });

  it('snoozeEntry keeps until, tier, source and wake', () => {
    const until = at(2026, 10, 24, 13);
    expect(snoozeEntry(task(), until)).toEqual({ until: until.toISOString(), tier: 'today', from: 'schedules', wake: { count: 3 } });
  });
});

describe('parseSnoozes', () => {
  const good = entry({ count: 3 });

  it('reads a stored map and drops expired entries', () => {
    const raw = JSON.stringify({ 'sched:7': good, 'sched:8': { ...good, until: at(2026, 10, 24, 9).toISOString() } });
    expect(parseSnoozes(raw, SAT)).toEqual({ 'sched:7': good });
  });

  it('corrupt or empty values read as nothing', () => {
    expect(parseSnoozes(null, SAT)).toEqual({});
    expect(parseSnoozes('{', SAT)).toEqual({});
    expect(parseSnoozes('[]', SAT)).toEqual({});
    expect(parseSnoozes('"x"', SAT)).toEqual({});
  });

  it('drops malformed entries and keeps the rest', () => {
    const raw = JSON.stringify({
      a: { ...good, until: 'nope' },
      b: { ...good, tier: 'sometime' },
      c: { ...good, from: 'nowhere' },
      c2: { ...good, from: 'toString' },
      b2: { ...good, tier: 'constructor' },
      d: { ...good, wake: null },
      e: { ...good, wake: { count: '3' } },
      f: { ...good, wake: { members: [1] } },
      g: 'x',
      ok: good,
    });
    expect(parseSnoozes(raw, SAT)).toEqual({ ok: good });
  });
});
