import { CategoryExpense, MonthSummary } from '../../../models/accounting.model';

/** 其他 swatch: canvas `C.other` (#8a94a6), defined in `styles.scss` for both themes. */
export const OTHER_COLOR = 'var(--app-category-other)';
/** A root category without its own colour (visible on the soft summary background). */
export const FALLBACK_COLOR = 'var(--app-text-muted)';

const TOP_COUNT = 5;
const OTHER_NAME = '其他';
const ARIA_PREFIX = '支出類別分佈';

export interface DistributionSegment {
  name: string;
  /** Positive magnitude used as the flex weight; the segments sum to |summary.expense|. */
  amount: number;
  /** Thousands-grouped integer, no sign or currency (display only; may differ from the header by rounding). */
  label: string;
  color: string;
  other: boolean;
}

/** Amounts are 4 dp decimals; drop float noise from the subtraction before comparing with zero. */
function quantize(value: number): number {
  return Math.round(value * 1e4) / 1e4;
}

function segment(name: string, amount: number, color: string, other: boolean): DistributionSegment {
  return { name, amount, label: Math.round(amount).toLocaleString('en-US'), color, other };
}

/**
 * 支出類別分佈 for the month: the five largest spending roots plus 其他, summing to |expense| by construction.
 * - remainder = |expense| − Σ top five ≥ 0 → raw top five, plus 其他 = remainder when > 0 (tail, 未分類,
 *   refund-only roots; a refund-only month has no top and becomes a single 其他 = |expense|);
 * - remainder < 0 (refunds outweigh the tail, or FX quantize residue) → top five scaled to |expense|, no 其他.
 * Equal magnitudes rank by name (`zh-Hant` collation) so the top five never depend on the API row order.
 */
export function categoryDistribution(summary: MonthSummary | null): DistributionSegment[] {
  const target = Math.abs(Number(summary?.expense ?? 0));
  const items = summary?.by_category ?? [];
  if (!(target > 0) || items.length === 0) return [];

  const top = items
    .map((c: CategoryExpense) => ({ c, spend: Number(c.expense) }))
    .filter(x => x.spend < 0)
    .map(x => ({ name: x.c.name, color: x.c.color ?? FALLBACK_COLOR, amount: -x.spend }))
    .sort((a, b) => b.amount - a.amount || a.name.localeCompare(b.name, 'zh-Hant'))
    .slice(0, TOP_COUNT);
  const topSum = top.reduce((sum, t) => sum + t.amount, 0);
  const remainder = quantize(target - topSum);

  if (remainder < 0) {
    const factor = target / topSum;
    return top.map(t => segment(t.name, t.amount * factor, t.color, false));
  }
  const segments = top.map(t => segment(t.name, t.amount, t.color, false));
  if (remainder > 0) segments.push(segment(OTHER_NAME, target - topSum, OTHER_COLOR, true));
  return segments;
}

/** `role="img"` label for the bar (phone has no legend, so this is its only legend). */
export function distributionAriaText(segments: readonly DistributionSegment[]): string {
  if (segments.length === 0) return ARIA_PREFIX;
  return `${ARIA_PREFIX}：${segments.map(s => `${s.name} ${s.label}`).join('、')}`;
}
