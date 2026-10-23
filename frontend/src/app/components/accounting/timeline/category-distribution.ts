import { CategoryExpense, MonthSummary } from '../../../models/accounting.model';

/** 其他 swatch: canvas `C.other` (#8a94a6), defined in `styles.scss` for both themes. */
export const OTHER_COLOR = 'var(--app-category-other)';
/** A root category without its own colour (visible on the soft summary background). */
export const FALLBACK_COLOR = 'var(--app-text-muted)';

const TOP_COUNT = 5;
/** Smallest 其他 / 退款 worth a legend line (amounts are 4 dp; anything below is FX quantize residue). */
const MIN_LINE = 0.01;
const OTHER_NAME = '其他';
const REFUND_NAME = '退款';
const ARIA_PREFIX = '支出類別分佈';

/** `category` = one of the top five roots; `other` = the tail; `refund` = Σ net-refund roots (legend only). */
export type DistributionKind = 'category' | 'other' | 'refund';

export interface DistributionSegment {
  name: string;
  /** Real magnitude in the main currency (positive); the bar's flex weight for `category` / `other`. */
  amount: number;
  /** Thousands-grouped integer, no sign or currency (display only; may differ from the header by rounding). */
  label: string;
  /** Swatch / bar colour; `null` for the legend-only 退款 line. */
  color: string | null;
  kind: DistributionKind;
}

function segment(name: string, amount: number, color: string | null, kind: DistributionKind): DistributionSegment {
  return { name, amount, label: Math.round(amount).toLocaleString('en-US'), color, kind };
}

/**
 * 支出類別分佈 for the month (Decision 3 rev 3), real amounts, nothing scaled:
 * - `expense ≥ 0` (no spending, or a net-refund month) or no `by_category` → `[]` (no bar);
 * - the five largest spending roots (`expense < 0`), equal magnitudes ranked by name (`zh-Hant` collation)
 *   so the top five never depend on the API row order;
 * - 其他 = Σ spending roots − Σ top five (the tail incl. 未分類), when ≥ 0.01;
 * - 退款 = Σ net-refund roots (`expense > 0`), last, when ≥ 0.01 — a legend line only, no bar segment.
 * Σ top + 其他 − 退款 = |expense| up to the per-bucket FX quantising (each (root, currency) bucket is converted
 * and rounded to 4 dp on its own); that residue is not a client concern and never becomes a segment.
 */
export function categoryDistribution(summary: MonthSummary | null): DistributionSegment[] {
  const expense = Number(summary?.expense ?? 0);
  const items = summary?.by_category ?? [];
  if (!(expense < 0) || items.length === 0) return [];

  const values = items.map((c: CategoryExpense) => ({ c, value: Number(c.expense) }));
  const negatives = values
    .filter(x => x.value < 0)
    .map(x => ({ name: x.c.name, color: x.c.color ?? FALLBACK_COLOR, amount: -x.value }))
    .sort((a, b) => b.amount - a.amount || a.name.localeCompare(b.name, 'zh-Hant'));
  if (negatives.length === 0) return []; // inconsistent payload (expense < 0 without a spending root): no bar
  const top = negatives.slice(0, TOP_COUNT);
  const tail = negatives.slice(TOP_COUNT).reduce((sum, n) => sum + n.amount, 0);
  const refund = values.filter(x => x.value > 0).reduce((sum, x) => sum + x.value, 0);

  const segments = top.map(t => segment(t.name, t.amount, t.color, 'category'));
  if (tail >= MIN_LINE) segments.push(segment(OTHER_NAME, tail, OTHER_COLOR, 'other'));
  if (refund >= MIN_LINE) segments.push(segment(REFUND_NAME, refund, null, 'refund'));
  return segments;
}

/** The segments drawn in the bar: everything but the legend-only 退款 line. */
export function barSegments(segments: readonly DistributionSegment[]): DistributionSegment[] {
  return segments.filter(s => s.kind !== 'refund');
}

/** `role="img"` label for the bar: every legend line in order, 退款 included (phone has no legend, so this is its only legend). */
export function distributionAriaText(segments: readonly DistributionSegment[]): string {
  if (segments.length === 0) return ARIA_PREFIX;
  return `${ARIA_PREFIX}：${segments.map(s => `${s.name} ${s.label}`).join('、')}`;
}
