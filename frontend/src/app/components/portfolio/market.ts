import { MarketCode } from '../../models/portfolio.model';

/** Market copy by code: TW → 台股, US → 美股, LSE → 英股. */
export const MARKET_LABELS: Record<MarketCode, string> = { TW: '台股', US: '美股', LSE: '英股' };

/** The market's label; an unknown code reads as itself, a missing one as ''. */
export function marketLabel(code: string | null | undefined): string {
  if (!code) return '';
  return Object.prototype.hasOwnProperty.call(MARKET_LABELS, code) ? MARKET_LABELS[code as MarketCode] : code;
}

/** Every market but TW is foreign; a missing code is not. */
export function isForeign(code: string | null | undefined): boolean {
  return !!code && code !== 'TW';
}
