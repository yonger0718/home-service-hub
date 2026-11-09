import { describe, expect, it } from 'vitest';

import { CategoryNode, DailySummaryDay } from '../../../models/accounting.model';
import { FALLBACK_COLOR } from '../timeline/category-distribution';

import {
  buildCalendar,
  cellAriaLabel,
  categoryColorMap,
  compactNet,
  heatRatio,
  maxDailyExpense,
  wideNet,
} from './calendar-math';

function node(id: number, color: string | null, children: CategoryNode[] = []): CategoryNode {
  return {
    id,
    kind: 'expense',
    parent_id: null,
    name: `c${id}`,
    icon: null,
    color,
    sort_order: 0,
    is_hidden: false,
    children,
  } as CategoryNode;
}

const OCTOBER: DailySummaryDay[] = [
  { date: '2026-10-05', expense: '0.0000', income: '50042.0000', count: 2 },
  { date: '2026-10-19', expense: '-12120.0000', income: '0.0000', count: 4 },
  { date: '2026-10-21', expense: '-3096.0000', income: '0.0000', count: 3 },
];

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

  it('crosses the year boundary for December 2026 Monday-first', () => {
    const cells = buildCalendar('2026-12', [], 1).flat();
    expect(cells[0]).toMatchObject({ date: '2026-11-30', outside: true });
    expect(cells[41]).toMatchObject({ date: '2027-01-10', outside: true });
  });
});

describe('compactNet', () => {
  it('formats thousands with one decimal and a k suffix', () => {
    expect(compactNet(-3096)).toBe('−3.1k');
    expect(compactNet(71700)).toBe('+71.7k');
    expect(compactNet(-12000)).toBe('−12k');
  });

  it('keeps whole signed values below 1000 and zero bare', () => {
    expect(compactNet(-250)).toBe('−250');
    expect(compactNet(71)).toBe('+71');
    expect(compactNet(0)).toBe('0');
  });

  it('rounds the magnitude to an integer before picking the branch', () => {
    expect(compactNet(-999.5)).toBe('−1k');
    expect(compactNet(-250.4)).toBe('−250');
    expect(compactNet(0.4)).toBe('0');
    expect(compactNet(-1000)).toBe('−1k');
    expect(compactNet(-99950)).toBe('−100k');
  });
});

describe('wideNet', () => {
  it('groups the absolute value with a sign and no decimals', () => {
    expect(wideNet(-3096)).toBe('−3,096');
    expect(wideNet(71700)).toBe('+71,700');
    expect(wideNet(0)).toBe('0');
  });

  it('rounds to an integer first and never prints a signed zero', () => {
    expect(wideNet(-0.4)).toBe('0');
    expect(wideNet(0.4)).toBe('0');
    expect(wideNet(-0.6)).toBe('−1');
  });
});

describe('heatRatio', () => {
  it('scales spend against the month maximum, capped at 1', () => {
    expect(heatRatio(-3096, 12120)).toBeCloseTo(0.255, 3);
    expect(heatRatio(-20000, 12120)).toBe(1);
  });

  it('is 0 for income, zero spend or no maximum', () => {
    expect(heatRatio(500, 12120)).toBe(0);
    expect(heatRatio(-5, 0)).toBe(0);
  });
});

describe('maxDailyExpense', () => {
  it('returns the largest spend magnitude across days', () => {
    expect(maxDailyExpense(OCTOBER)).toBe(12120);
  });

  it('returns 0 when no day has spend', () => {
    expect(maxDailyExpense([{ date: '2026-10-05', expense: '0.0000', income: '1.0000', count: 1 }])).toBe(0);
    expect(maxDailyExpense([])).toBe(0);
  });
});

describe('categoryColorMap', () => {
  it('gives a child without colour its parent colour and a colourless root the fallback', () => {
    const roots = [node(1, '#ff0000', [node(2, null, [node(3, null)])]), node(4, null)];
    const map = categoryColorMap(roots);
    expect(map.get(1)).toBe('#ff0000');
    expect(map.get(2)).toBe('#ff0000');
    expect(map.get(3)).toBe('#ff0000');
    expect(map.get(4)).toBe(FALLBACK_COLOR);
  });

  it('keeps a child\'s own colour over its parent', () => {
    const map = categoryColorMap([node(1, '#111111', [node(2, '#222222')])]);
    expect(map.get(2)).toBe('#222222');
  });
});

describe('cellAriaLabel', () => {
  const base = {
    date: '2026-10-21',
    count: 3,
    expense: -3096,
    income: 0,
    currency: 'TWD',
    bill: null as 'due' | 'paid' | null,
    dueCount: 0,
    invoices: 1,
  };

  it('names the date with weekday and joins the parts', () => {
    expect(cellAriaLabel(base)).toBe('10 月 21 日 週三，3 筆，支出 $3,096，發票待對帳');
  });

  it('ends with 已繳 for a paid bill', () => {
    const label = cellAriaLabel({ ...base, bill: 'paid', dueCount: 2, invoices: 0 });
    expect(label.endsWith('已繳')).toBe(true);
    expect(label).not.toContain('繳費到期');
  });

  it('names a Sunday as 週日', () => {
    expect(cellAriaLabel({ ...base, date: '2026-10-25', count: 0, expense: 0, invoices: 0 })).toBe('10 月 25 日 週日');
  });

  it('contains no quotation brackets', () => {
    expect(cellAriaLabel(base)).not.toMatch(/[「」]/);
  });

  it('adds income and omits empty parts', () => {
    expect(cellAriaLabel({ ...base, count: 0, expense: 0, income: 50042, invoices: 0, bill: 'due', dueCount: 1 })).toBe(
      '10 月 21 日 週三，收入 $50,042，1 張卡繳費到期',
    );
  });
});
