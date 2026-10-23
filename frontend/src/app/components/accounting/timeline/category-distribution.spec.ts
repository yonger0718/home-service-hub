import { describe, expect, it } from 'vitest';

import { CategoryExpense, MonthSummary } from '../../../models/accounting.model';
import {
  DistributionSegment,
  FALLBACK_COLOR,
  OTHER_COLOR,
  categoryDistribution,
  distributionAriaText,
} from './category-distribution';

function cat(name: string, expense: string, color: string | null = '#123456', category_id: number | null = 1): CategoryExpense {
  return { category_id, name, color, expense };
}

function summary(expense: string, by_category?: CategoryExpense[]): MonthSummary {
  return { month: '2026-10', currency: 'TWD', expense, income: '0', net: expense, missing_rates: [], by_category };
}

function total(segments: DistributionSegment[]): number {
  return segments.reduce((sum, s) => sum + s.amount, 0);
}

/** Canvas AccountingDesktop `:93-107` / TimelinePhone `:165-168`: five roots + three small ones folded into 其他 7,460. */
const CANVAS: CategoryExpense[] = [
  cat('餐飲', '-9820.0000', '#d0864a', 1),
  cat('居家', '-8150.0000', '#4f8f8a', 2),
  cat('日用', '-6930.0000', '#4f9a72', 3),
  cat('交通', '-3240.0000', '#5f86c9', 4),
  cat('娛樂', '-2860.0000', '#8a7cc4', 5),
  cat('飲料', '-2460.0000', '#a88a5c', 6),
  cat('搬家', '-2800.0000', '#7d889c', 7),
  cat('未分類', '-2200.0000', null, null),
];

describe('categoryDistribution', () => {
  it('builds the canvas bar: five named roots in order plus 其他 7,460, summing to the month expense', () => {
    const segments = categoryDistribution(summary('-38460.0000', CANVAS));

    expect(segments.map(s => s.name)).toEqual(['餐飲', '居家', '日用', '交通', '娛樂', '其他']);
    expect(segments.map(s => s.label)).toEqual(['9,820', '8,150', '6,930', '3,240', '2,860', '7,460']);
    expect(segments.map(s => s.amount)).toEqual([9820, 8150, 6930, 3240, 2860, 7460]);
    expect(segments.map(s => s.other)).toEqual([false, false, false, false, false, true]);
    expect(segments.map(s => s.color)).toEqual(['#d0864a', '#4f8f8a', '#4f9a72', '#5f86c9', '#8a7cc4', OTHER_COLOR]);
    expect(total(segments)).toBeCloseTo(38460, 6);
  });

  it('exports the two fallback colours as CSS variables', () => {
    expect(OTHER_COLOR).toBe('var(--app-category-other)');
    expect(FALLBACK_COLOR).toBe('var(--app-text-muted)');
  });

  it('paints a root without a colour with FALLBACK_COLOR', () => {
    const segments = categoryDistribution(summary('-300', [cat('未分類', '-200', null, null), cat('餐飲', '-100', '#d0864a')]));

    expect(segments).toEqual([
      { name: '未分類', amount: 200, label: '200', color: FALLBACK_COLOR, other: false },
      { name: '餐飲', amount: 100, label: '100', color: '#d0864a', other: false },
    ]);
  });

  it('ranks negative roots by magnitude regardless of input order', () => {
    const segments = categoryDistribution(summary('-600', [cat('小', '-100'), cat('大', '-300'), cat('中', '-200')]));

    expect(segments.map(s => s.name)).toEqual(['大', '中', '小']);
  });

  it('breaks equal magnitudes by name, so the top five and 其他 do not depend on the API row order', () => {
    const rows = [cat('F', '-100'), cat('C', '-100'), cat('A', '-100'), cat('E', '-100'), cat('B', '-100'), cat('D', '-100')];
    const forward = categoryDistribution(summary('-600', rows));
    const backward = categoryDistribution(summary('-600', [...rows].reverse()));

    expect(forward.map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E', '其他']);
    expect(backward).toEqual(forward);
  });

  it('applies the name tiebreak with zh-Hant collation for Chinese roots', () => {
    const rows = [cat('餐飲', '-100'), cat('交通', '-100'), cat('居家', '-100')];
    const expected = ['餐飲', '交通', '居家'].sort((a, b) => a.localeCompare(b, 'zh-Hant'));

    expect(categoryDistribution(summary('-300', rows)).map(s => s.name)).toEqual(expected);
    expect(categoryDistribution(summary('-300', [...rows].reverse())).map(s => s.name)).toEqual(expected);
  });

  it('absorb: the tail and a refund-only root fold into 其他 = target − Σ top five', () => {
    const items = [
      cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'),
      cat('F', '-80'), cat('退款', '30'),
    ];
    const segments = categoryDistribution(summary('-1550.0000', items));

    expect(segments.map(s => [s.name, s.amount])).toEqual([
      ['A', 500], ['B', 400], ['C', 300], ['D', 200], ['E', 100], ['其他', 50],
    ]);
    expect(segments[5]).toMatchObject({ other: true, color: OTHER_COLOR, label: '50' });
    expect(total(segments)).toBeCloseTo(1550, 6);
  });

  it('scale: a refund-heavy root outweighing the tail scales the top amounts to the target, no 其他', () => {
    const segments = categoryDistribution(summary('-700.0000', [cat('餐飲', '-800'), cat('居家', '-200'), cat('交通', '300')]));

    expect(segments.map(s => s.name)).toEqual(['餐飲', '居家']);
    expect(segments[0].amount).toBeCloseTo(560, 6);
    expect(segments[1].amount).toBeCloseTo(140, 6);
    expect(segments.map(s => s.label)).toEqual(['560', '140']);
    expect(segments.some(s => s.other)).toBe(false);
    expect(Math.abs(total(segments) - 700)).toBeLessThan(1e-6);
  });

  it('refund-only month: no negative roots, positive target → one 其他 = target', () => {
    const segments = categoryDistribution(summary('-150.0000', [cat('A', '100'), cat('B', '50')]));

    expect(segments).toEqual([{ name: '其他', amount: 150, label: '150', color: OTHER_COLOR, other: true }]);
  });

  it('refund-only month with a positive expense string still uses |expense|', () => {
    const segments = categoryDistribution(summary('150.0000', [cat('A', '100'), cat('B', '50')]));

    expect(segments).toEqual([{ name: '其他', amount: 150, label: '150', color: OTHER_COLOR, other: true }]);
  });

  it('exactly five negative roots and no positive root → remainder 0, no 其他', () => {
    const items = [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100')];
    const segments = categoryDistribution(summary('-1500', items));

    expect(segments.map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E']);
    expect(segments.some(s => s.other)).toBe(false);
    expect(total(segments)).toBeCloseTo(1500, 6);
  });

  it('five negative roots whose sum is below the target still get 其他 (no count shortcut)', () => {
    const items = [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100')];
    const segments = categoryDistribution(summary('-1540', items));

    expect(segments.map(s => [s.name, s.amount])).toEqual([
      ['A', 500], ['B', 400], ['C', 300], ['D', 200], ['E', 100], ['其他', 40],
    ]);
    expect(total(segments)).toBeCloseTo(1540, 6);
  });

  it('five negative roots plus a net-positive root with Σ top > target → scale, no 其他', () => {
    const items = [
      cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('退款', '300'),
    ];
    const segments = categoryDistribution(summary('-1200', items));
    const factor = 1200 / 1500;

    expect(segments.map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E']);
    [500, 400, 300, 200, 100].forEach((raw, i) => expect(segments[i].amount).toBeCloseTo(raw * factor, 6));
    expect(segments.some(s => s.other)).toBe(false);
    expect(Math.abs(total(segments) - 1200)).toBeLessThan(1e-6);
  });

  it('four roots below |expense| (未分類 / rounding) → 其他 = remainder', () => {
    const items = [cat('A', '-400'), cat('B', '-300'), cat('C', '-200'), cat('D', '-50')];
    const segments = categoryDistribution(summary('-1000', items));

    expect(segments.map(s => [s.name, s.amount])).toEqual([['A', 400], ['B', 300], ['C', 200], ['D', 50], ['其他', 50]]);
    expect(total(segments)).toBeCloseTo(1000, 6);
  });

  it('sub-cent FX residue (target 999.9999 vs Σ top 1000.0000) → scale branch, Σ == target', () => {
    const items = [cat('A', '-600.0000'), cat('B', '-400.0000')];
    const segments = categoryDistribution(summary('-999.9999', items));

    expect(segments.map(s => s.name)).toEqual(['A', 'B']);
    expect(segments.some(s => s.other)).toBe(false);
    expect(Math.abs(total(segments) - 999.9999)).toBeLessThan(1e-6);
    expect(segments.every(s => s.amount > 0)).toBe(true);
  });

  it('Σ amounts == |expense| in every branch, no zero or negative segment', () => {
    const cases: MonthSummary[] = [
      summary('-38460.0000', CANVAS),
      summary('-1550.0000', [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('F', '-80'), cat('R', '30')]),
      summary('-700.0000', [cat('餐飲', '-800'), cat('居家', '-200'), cat('交通', '300')]),
      summary('-150.0000', [cat('A', '100'), cat('B', '50')]),
      summary('-999.9999', [cat('A', '-600.0000'), cat('B', '-400.0000')]),
      summary('-1000', [cat('A', '-400'), cat('B', '-300'), cat('C', '-200'), cat('D', '-50')]),
    ];
    for (const s of cases) {
      const segments = categoryDistribution(s);
      expect(Math.abs(total(segments) - Math.abs(Number(s.expense)))).toBeLessThan(1e-6);
      expect(segments.every(seg => seg.amount > 0)).toBe(true);
    }
  });

  it('no bar for a null summary, expense 0, an absent or an empty by_category', () => {
    expect(categoryDistribution(null)).toEqual([]);
    expect(categoryDistribution(summary('0', [cat('A', '-100')]))).toEqual([]);
    expect(categoryDistribution(summary('0.0000', [cat('A', '100')]))).toEqual([]);
    expect(categoryDistribution(summary('-500.0000'))).toEqual([]);
    expect(categoryDistribution(summary('-500.0000', []))).toEqual([]);
  });

  it('labels are Math.round + en-US grouping, without sign or currency (may differ from the header by rounding)', () => {
    const segments = categoryDistribution(summary('-13581', [cat('A', '-12345.5'), cat('B', '-1234.4999'), cat('C', '-0.4')]));

    expect(segments.map(s => s.label)).toEqual(['12,346', '1,234', '0', '1']);
    expect(segments[3]).toMatchObject({ name: '其他', other: true });
    expect(segments[3].amount).toBeCloseTo(0.6001, 6);
  });
});

describe('distributionAriaText', () => {
  it('joins name + label with 、 after 支出類別分佈：', () => {
    expect(distributionAriaText(categoryDistribution(summary('-38460.0000', CANVAS)))).toBe(
      '支出類別分佈：餐飲 9,820、居家 8,150、日用 6,930、交通 3,240、娛樂 2,860、其他 7,460',
    );
  });

  it('returns the bare label for no segments', () => {
    expect(distributionAriaText([])).toBe('支出類別分佈');
  });
});
