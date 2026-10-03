import { describe, expect, it } from 'vitest';

import { buildCalendar } from './calendar-math';

describe('buildCalendar', () => {
  it('lays February 2026 out Monday-first from Mon 2026-01-26 over 6 rows', () => {
    const rows = buildCalendar('2026-02', [], 1);
    expect(rows.length).toBe(6);
    expect(rows.every(row => row.length === 7)).toBe(true);
    expect(rows[0][0]).toMatchObject({ date: '2026-01-26', day: 26, outside: true });
    expect(rows[0][6]).toMatchObject({ date: '2026-02-01', day: 1, outside: false });
    expect(rows[5][6]).toMatchObject({ date: '2026-03-08', outside: true });
  });

  it('lays February 2026 out Sunday-first', () => {
    const rows = buildCalendar('2026-02', [], 0);
    expect(rows.length).toBe(6);
    expect(rows[0][0]).toMatchObject({ date: '2026-02-01', outside: false });
    expect(rows[3][6]).toMatchObject({ date: '2026-02-28', outside: false });
    expect(rows[4][0]).toMatchObject({ date: '2026-03-01', outside: true });
    expect(rows[5][6]).toMatchObject({ date: '2026-03-14', outside: true });
  });

  it('starts on day 1 when the month begins on the week-start day and fills the 6 rows from the next month', () => {
    // 2026-06-01 is a Monday: no leading cells; the remaining cells of the 6 × 7 grid are trailing July days.
    const cells = buildCalendar('2026-06', [], 1).flat();
    expect(cells.length).toBe(42);
    expect(cells[0]).toMatchObject({ date: '2026-06-01', outside: false });
    expect(cells.filter(cell => !cell.outside).length).toBe(30);
    expect(cells[30]).toMatchObject({ date: '2026-07-01', outside: true });
    expect(cells[41].date).toBe('2026-07-12');
  });

  it('maps the figures by date and keeps outside cells empty', () => {
    const cells = buildCalendar(
      '2026-10',
      [
        { date: '2026-10-02', expense: '-815.0000', income: '0.0000', count: 3 },
        { date: '2026-10-05', expense: '0.0000', income: '50042.0000', count: 2 },
        { date: '2026-09-30', expense: '-99.0000', income: '0.0000', count: 1 },
      ],
      0,
    ).flat();
    expect(cells.find(cell => cell.date === '2026-10-02')).toMatchObject({ expense: -815, income: 0, count: 3 });
    expect(cells.find(cell => cell.date === '2026-10-05')).toMatchObject({ expense: 0, income: 50042, count: 2 });
    expect(cells.find(cell => cell.date === '2026-10-03')).toMatchObject({ expense: 0, income: 0, count: 0 });
    expect(cells.find(cell => cell.date === '2026-09-30')).toMatchObject({ outside: true, expense: 0, income: 0, count: 0 });
  });
});
