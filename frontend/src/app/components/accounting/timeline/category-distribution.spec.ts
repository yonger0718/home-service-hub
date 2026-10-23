import { describe, expect, it } from 'vitest';

import { CategoryExpense, MonthSummary } from '../../../models/accounting.model';
import {
  DistributionSegment,
  FALLBACK_COLOR,
  OTHER_COLOR,
  barSegments,
  categoryDistribution,
  distributionAriaText,
} from './category-distribution';

function cat(name: string, expense: string, color: string | null = '#123456', category_id: number | null = 1): CategoryExpense {
  return { category_id, name, color, expense };
}

function summary(expense: string, by_category?: CategoryExpense[]): MonthSummary {
  return { month: '2026-10', currency: 'TWD', expense, income: '0', net: expense, missing_rates: [], by_category };
}

/** Σ top + 其他 − 退款: the arithmetic the legend shows, reconciling to |expense|. */
function reconciled(segments: DistributionSegment[]): number {
  return segments.reduce((sum, s) => sum + (s.kind === 'refund' ? -s.amount : s.amount), 0);
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
    expect(segments.map(s => s.kind)).toEqual(['category', 'category', 'category', 'category', 'category', 'other']);
    expect(segments.map(s => s.color)).toEqual(['#d0864a', '#4f8f8a', '#4f9a72', '#5f86c9', '#8a7cc4', OTHER_COLOR]);
    expect(reconciled(segments)).toBeCloseTo(38460, 6);
  });

  it('exports the two fallback colours as CSS variables', () => {
    expect(OTHER_COLOR).toBe('var(--app-category-other)');
    expect(FALLBACK_COLOR).toBe('var(--app-text-muted)');
  });

  it('paints a root without a colour with FALLBACK_COLOR', () => {
    const segments = categoryDistribution(summary('-300', [cat('未分類', '-200', null, null), cat('餐飲', '-100', '#d0864a')]));

    expect(segments).toEqual([
      { name: '未分類', amount: 200, label: '200', color: FALLBACK_COLOR, kind: 'category' },
      { name: '餐飲', amount: 100, label: '100', color: '#d0864a', kind: 'category' },
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

  it('退款: a net-refund root becomes a legend-only 退款 line with real amounts, no 其他', () => {
    const segments = categoryDistribution(summary('-700.0000', [cat('餐飲', '-800'), cat('居家', '-200'), cat('交通', '300')]));

    expect(segments).toEqual([
      { name: '餐飲', amount: 800, label: '800', color: '#123456', kind: 'category' },
      { name: '居家', amount: 200, label: '200', color: '#123456', kind: 'category' },
      { name: '退款', amount: 300, label: '300', color: null, kind: 'refund' },
    ]);
    expect(barSegments(segments).map(s => s.name)).toEqual(['餐飲', '居家']);
    expect(distributionAriaText(segments)).toBe('支出類別分佈：餐飲 800、居家 200、退款 300');
    expect(reconciled(segments)).toBeCloseTo(700, 6);
  });

  it('top five + 其他 (the tail) + 退款 (Σ net-refund roots), 退款 last', () => {
    const items = [
      cat('A', '-500'), cat('B', '-400'), cat('R', '30'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('F', '-80'),
    ];
    const segments = categoryDistribution(summary('-1550.0000', items));

    expect(segments.map(s => [s.name, s.amount, s.kind])).toEqual([
      ['A', 500, 'category'], ['B', 400, 'category'], ['C', 300, 'category'], ['D', 200, 'category'], ['E', 100, 'category'],
      ['其他', 80, 'other'], ['退款', 30, 'refund'],
    ]);
    expect(segments[5]).toMatchObject({ color: OTHER_COLOR, label: '80' });
    expect(segments[6]).toMatchObject({ color: null, label: '30' });
    expect(barSegments(segments).map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E', '其他']);
    expect(reconciled(segments)).toBeCloseTo(1550, 6);
  });

  it('refund-only month (expense ≥ 0) → no bar', () => {
    expect(categoryDistribution(summary('150.0000', [cat('A', '100'), cat('B', '50')]))).toEqual([]);
  });

  it('net-refund month with some spending (expense > 0) → no bar', () => {
    expect(categoryDistribution(summary('200.0000', [cat('餐飲', '-100'), cat('交通', '300')]))).toEqual([]);
  });

  it('exactly five negative roots and no positive root → no 其他, no 退款', () => {
    const items = [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100')];
    const segments = categoryDistribution(summary('-1500', items));

    expect(segments.map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E']);
    expect(segments.every(s => s.kind === 'category')).toBe(true);
    expect(reconciled(segments)).toBeCloseTo(1500, 6);
  });

  it('omits a sub-cent 其他 tail (FX quantize residue), keeps a one-cent tail', () => {
    const top = [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100')];
    const subCent = categoryDistribution(summary('-1500.0040', [...top, cat('F', '-0.0040')]));
    const oneCent = categoryDistribution(summary('-1500.0100', [...top, cat('F', '-0.0100')]));

    expect(subCent.map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E']);
    expect(oneCent.map(s => s.name)).toEqual(['A', 'B', 'C', 'D', 'E', '其他']);
    expect(oneCent[5]).toMatchObject({ kind: 'other', label: '0' });
    expect(oneCent[5].amount).toBeCloseTo(0.01, 6);
  });

  it('omits a sub-cent 退款 line', () => {
    const segments = categoryDistribution(summary('-99.9960', [cat('A', '-100'), cat('R', '0.0040')]));

    expect(segments.map(s => s.name)).toEqual(['A']);
  });

  it('never scales: an FX residue between expense and Σ by_category stays out of the segments', () => {
    const segments = categoryDistribution(summary('-999.9999', [cat('A', '-600.0000'), cat('B', '-400.0000')]));

    expect(segments.map(s => [s.name, s.amount])).toEqual([['A', 600], ['B', 400]]);
  });

  it('Σ top + 其他 − 退款 == |expense| for rated fixtures, no zero or negative amount', () => {
    const cases: MonthSummary[] = [
      summary('-38460.0000', CANVAS),
      summary('-1550.0000', [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('F', '-80'), cat('R', '30')]),
      summary('-700.0000', [cat('餐飲', '-800'), cat('居家', '-200'), cat('交通', '300')]),
      summary('-1200.0000', [cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('R', '300')]),
      summary('-1000.0000', [cat('A', '-400'), cat('B', '-300'), cat('C', '-200'), cat('D', '-50'), cat('未分類', '-50', null, null)]),
    ];
    for (const s of cases) {
      const segments = categoryDistribution(s);
      expect(reconciled(segments)).toBeCloseTo(Math.abs(Number(s.expense)), 6);
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

  it('no bar for an inconsistent payload without any spending root', () => {
    expect(categoryDistribution(summary('-5.0000', [cat('R', '30')]))).toEqual([]);
  });

  it('labels are Math.round + en-US grouping, without sign or currency (may differ from the header by rounding)', () => {
    const items = [cat('A', '-12345.5'), cat('B', '-1234.4999'), cat('C', '-3'), cat('D', '-2'), cat('E', '-1'), cat('F', '-0.6001')];
    const segments = categoryDistribution(summary('-13586.6', items));

    expect(segments.map(s => s.label)).toEqual(['12,346', '1,234', '3', '2', '1', '1']);
    expect(segments[5]).toMatchObject({ name: '其他', kind: 'other' });
    expect(segments[5].amount).toBeCloseTo(0.6001, 6);
  });
});

describe('barSegments', () => {
  it('keeps every line but 退款, in order', () => {
    const segments = categoryDistribution(summary('-1550.0000', [
      cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('F', '-80'), cat('R', '30'),
    ]));

    expect(barSegments(segments)).toEqual(segments.slice(0, 6));
    expect(barSegments([])).toEqual([]);
  });
});

describe('distributionAriaText', () => {
  it('joins name + label with 、 after 支出類別分佈：', () => {
    expect(distributionAriaText(categoryDistribution(summary('-38460.0000', CANVAS)))).toBe(
      '支出類別分佈：餐飲 9,820、居家 8,150、日用 6,930、交通 3,240、娛樂 2,860、其他 7,460',
    );
  });

  it('lists the 退款 line last', () => {
    const segments = categoryDistribution(summary('-1550.0000', [
      cat('A', '-500'), cat('B', '-400'), cat('C', '-300'), cat('D', '-200'), cat('E', '-100'), cat('F', '-80'), cat('R', '30'),
    ]));

    expect(distributionAriaText(segments)).toBe('支出類別分佈：A 500、B 400、C 300、D 200、E 100、其他 80、退款 30');
  });

  it('returns the bare label for no segments', () => {
    expect(distributionAriaText([])).toBe('支出類別分佈');
  });
});
