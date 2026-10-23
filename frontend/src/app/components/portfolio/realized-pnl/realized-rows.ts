import { RealizedPnlEvent } from '../../../models/portfolio.model';
import { isForeign, marketLabel } from '../market';
import { formatFxRate, formatNativeAmount, groupedInteger, isTwdCurrency } from '../money';
import { StockSquare, stockSquare } from '../square';
import { signedInteger } from './realized-math';

/** One line of an expanded sell: `main` native for a foreign sell (with the TWD figure in `sub`), else TWD. */
export interface RealizedDetail {
  label: string;
  main: string;
  sub: string | null;
  /** Trend colour of `main`, from the sign of the value it shows (only 已實現損益 carries one). */
  tone: 'up' | 'down' | '';
}

/** A sell as the realized list draws it (Decision 4). */
export interface RealizedRow {
  key: string;
  square: StockSquare;
  name: string;
  symbol: string;
  marketNote: string | null;
  dayTrade: boolean;
  meta: string;
  amount: string;
  amountClass: 'up' | 'down' | '';
  pct: string | null;
  detail: RealizedDetail[];
  warnings: string[];
}

/** The 融券 marker (shown as a tag); every other warning is an icon whose aria-label is the warning text. */
export const SHORT_WARNING = '融券';

const NOTE_WARNINGS: Record<string, string> = {
  no_long_inventory: 'LONG 賣出無現股庫存，成本以 0 計算',
  no_short_inventory: 'SHORT 回補無未平倉空單，視為純成本',
};

function finite(value: string | number | null | undefined): number | null {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Whole TWD, `—` when not numeric. */
function twd(value: string | number | null | undefined): string {
  const n = finite(value);
  return n == null ? '—' : groupedInteger(n);
}

function signedTwd(value: string | number | null | undefined): string {
  const n = finite(value);
  return n == null ? '—' : signedInteger(n);
}

function toneOf(shown: number): 'up' | 'down' | '' {
  return shown > 0 ? 'up' : shown < 0 ? 'down' : '';
}

/** A native amount as shown (rounded to its decimals), so its sign matches the text. */
function shownNative(value: number, currency: string | null): number {
  return Number(value.toFixed(currency === 'GBp' ? 4 : 2)) || 0;
}

/** A TW price or average, 2 dp grouped (`1,106.58`). */
function price(value: string | number | null | undefined): string {
  const n = finite(value);
  return n == null ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function shares(quantity: number): string {
  return Number(quantity).toLocaleString('en-US', { maximumFractionDigits: 4 });
}

/** A native amount with its sign (`+184.50 USD`, `−3.20 USD`); no sign when it shows as zero. */
function signedNative(value: number | null, currency: string | null): string {
  if (value == null) return '—';
  const shown = shownNative(value, currency);
  return `${shown > 0 ? '+' : shown < 0 ? '−' : ''}${formatNativeAmount(Math.abs(value), currency)}`;
}

/** `realized_pnl / cost_out × 100`, 1 dp, signed (`+8.4%`, `−7.3%`); null when there is no cost to compare with. */
function pct(e: RealizedPnlEvent): string | null {
  const cost = finite(e.cost_out);
  const pnl = finite(e.realized_pnl);
  if (!cost || pnl == null) return null;
  const ratio = (pnl / cost) * 100;
  const abs = Math.abs(ratio).toFixed(1);
  if (abs === '0.0') return '0.0%';
  return `${ratio > 0 ? '+' : '−'}${abs}%`;
}

/**
 * The TWD-per-unit rate implied by a sell's two gross amounts. Local because it is derived per sell, not a stored
 * rate: TWD gross is whole dollars, so it is only good to about 4 significant digits (32,800 / 1,061.50 → 30.90).
 */
function impliedFxRate(twdGross: number, nativeGross: number): number {
  return Number((twdGross / nativeGross).toPrecision(4));
}

/**
 * Stable row identity; several rows of the same sell stay apart in `realizedRows`. Fields that tell sells apart
 * without being unique (no id is sent).
 */
function rowKey(e: RealizedPnlEvent): string {
  return [e.trade_date, e.symbol, e.market, e.quantity, e.sell_price, e.realized_pnl].join('|');
}

export function realizedRow(e: RealizedPnlEvent, name: string | null, brokerLabel: string): RealizedRow {
  const foreign = e.market !== 'TW' && !isTwdCurrency(e.native_currency);
  const currency = e.native_currency;
  const qty = Number(e.quantity);
  const nativeCost = finite(e.native_cost);
  const avg = foreign ? (nativeCost == null || !qty ? '—' : formatNativeAmount(nativeCost / qty, currency)) : price(e.avg_cost_at_sale);
  const sell = foreign ? formatNativeAmount(e.native_sell_price ?? null, currency) : price(e.sell_price);
  const meta = [`${e.trade_date.slice(5, 7)}/${e.trade_date.slice(8, 10)}`, `${shares(e.quantity)} 股`, `均價 ${avg} → 賣出 ${sell}`];
  if (brokerLabel) meta.push(brokerLabel);

  const whole = Math.round(finite(e.realized_pnl) ?? 0) || 0;
  const line = (label: string, value: string | number | null | undefined, native: string | number | null | undefined): RealizedDetail =>
    foreign
      ? { label, main: formatNativeAmount(native ?? null, currency), sub: twd(value), tone: '' }
      : { label, main: twd(value), sub: null, tone: '' };
  const nativeProceeds = finite(e.native_proceeds);
  const nativePnl = nativeProceeds == null || nativeCost == null ? null : nativeProceeds - nativeCost;
  const detail: RealizedDetail[] = [
    line('成交總額', e.proceeds_gross, e.native_proceeds_gross),
    line('手續費', e.fee, e.native_fee),
    line(e.is_day_trade ? '交易稅（當沖）' : '交易稅', e.tax, e.native_tax),
    line('淨收入', e.proceeds_net, e.native_proceeds),
    line('賣出成本', e.cost_out, e.native_cost),
    foreign
      ? {
          label: '已實現損益',
          main: signedNative(nativePnl, currency),
          sub: signedTwd(e.realized_pnl),
          tone: nativePnl == null ? '' : toneOf(shownNative(nativePnl, currency)),
        }
      : { label: '已實現損益', main: signedTwd(e.realized_pnl), sub: null, tone: toneOf(whole) },
  ];
  const nativeGross = finite(e.native_proceeds_gross);
  const gross = finite(e.proceeds_gross);
  if (foreign && nativeGross != null && nativeGross > 0 && gross != null) {
    detail.push({ label: '匯率', main: formatFxRate(impliedFxRate(gross, nativeGross)), sub: null, tone: '' });
  }

  const warnings: string[] = [];
  if (e.position_side === 'SHORT') warnings.push(SHORT_WARNING);
  if (e.note && NOTE_WARNINGS[e.note]) warnings.push(NOTE_WARNINGS[e.note]);

  return {
    key: rowKey(e),
    square: stockSquare(e.symbol, name),
    name: name || e.symbol,
    symbol: e.symbol,
    marketNote: isForeign(e.market) ? marketLabel(e.market) : null,
    dayTrade: e.is_day_trade,
    meta: meta.join(' · '),
    amount: signedTwd(e.realized_pnl),
    amountClass: toneOf(whole),
    pct: pct(e),
    detail,
    warnings,
  };
}

/** The list's rows in order; a repeated key (the same sell twice) gets a `#n` suffix so rows stay distinct. */
export function realizedRows(
  events: readonly RealizedPnlEvent[],
  nameOf: (e: RealizedPnlEvent) => string | null,
  brokerOf: (e: RealizedPnlEvent) => string,
): RealizedRow[] {
  const seen = new Map<string, number>();
  return events.map(e => {
    const row = realizedRow(e, nameOf(e), brokerOf(e));
    const n = seen.get(row.key) ?? 0;
    seen.set(row.key, n + 1);
    return n ? { ...row, key: `${row.key}#${n}` } : row;
  });
}
