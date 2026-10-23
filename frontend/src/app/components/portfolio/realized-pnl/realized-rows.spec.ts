import { describe, expect, it } from 'vitest';

import { RealizedPnlEvent, brokerLabel } from '../../../models/portfolio.model';
import { stockSquare } from '../square';
import { REALIZED_FIXTURE } from './realized-fixture.spec-data';
import { SHORT_WARNING, realizedRow, realizedRows } from './realized-rows';

const [HONHAI, TSMC, AAPL] = REALIZED_FIXTURE;

function row(e: RealizedPnlEvent) {
  return realizedRow(e, e.name, brokerLabel(e.broker));
}

function detail(e: RealizedPnlEvent) {
  return row(e).detail.map(d => [d.label, d.main, d.sub]);
}

describe('realizedRow', () => {
  it('builds the 鴻海 row: square, name, meta, amount, pct', () => {
    const r = row(HONHAI);
    expect(r.square).toEqual(stockSquare('2317', '鴻海'));
    expect(r.name).toBe('鴻海');
    expect(r.symbol).toBe('2317');
    expect(r.marketNote).toBeNull();
    expect(r.dayTrade).toBe(false);
    expect(r.meta).toBe(`10/02 · 1,000 股 · 均價 160.73 → 賣出 175.00 · ${brokerLabel('TW_CATHAY')}`);
    expect(r.amount).toBe('+13,498');
    expect(r.amountClass).toBe('up');
    expect(r.pct).toBe('+8.4%');
    expect(r.warnings).toEqual([]);
  });

  it('lists the 鴻海 detail in order, TWD only, without 匯率', () => {
    expect(detail(HONHAI)).toEqual([
      ['成交總額', '175,000', null],
      ['手續費', '249', null],
      ['交易稅', '525', null],
      ['淨收入', '174,226', null],
      ['賣出成本', '160,728', null],
      ['已實現損益', '+13,498', null],
    ]);
  });

  it('labels the day-trade tax 交易稅（當沖）and flags the row', () => {
    const r = row(TSMC);
    expect(r.dayTrade).toBe(true);
    expect(r.detail.map(d => d.label)).toEqual(['成交總額', '手續費', '交易稅（當沖）', '淨收入', '賣出成本', '已實現損益']);
    expect(r.meta).toBe(`08/21 · 200 股 · 均價 1,106.58 → 賣出 1,120.00 · ${brokerLabel('TW_CATHAY')}`);
    expect(r.pct).toBe('+0.9%');
  });

  it('shows a foreign sell in native figures with TWD subs and the 匯率', () => {
    const r = row(AAPL);
    expect(r.marketNote).toBe('美股');
    expect(r.meta).toBe(`06/12 · 5 股 · 均價 175.20 USD → 賣出 212.30 USD · ${brokerLabel('FIRSTRADE')}`);
    expect(r.amount).toBe('+5,701');
    expect(r.pct).toBe('+21.1%');
    expect(detail(AAPL)).toEqual([
      ['成交總額', '1,061.50 USD', '32,800'],
      ['手續費', '1.00 USD', '31'],
      ['交易稅', '0.00 USD', '0'],
      ['淨收入', '1,060.50 USD', '32,769'],
      ['賣出成本', '876.00 USD', '27,068'],
      ['已實現損益', '+184.50 USD', '+5,701'],
      ['匯率', '30.90', null],
    ]);
  });

  it('tones only 已實現損益, by the sign of the value it shows', () => {
    expect(row(HONHAI).detail.map(d => d.tone)).toEqual(['', '', '', '', '', 'up']);
    expect(row(REALIZED_FIXTURE[3]).detail.at(-1)?.tone).toBe('down');
    // A native gain that FX turns into a TWD loss: the native main is green, the TWD sub and the row amount red.
    const flipped = row({ ...AAPL, realized_pnl: '-120' });
    const pnl = flipped.detail.find(d => d.label === '已實現損益')!;
    expect([pnl.main, pnl.sub, pnl.tone]).toEqual(['+184.50 USD', '−120', 'up']);
    expect(flipped.amountClass).toBe('down');
    expect(row({ ...AAPL, native_proceeds: '876.004' }).detail.find(d => d.label === '已實現損益')?.tone).toBe('');
  });

  it('hides the pct when cost_out is 0', () => {
    expect(row({ ...HONHAI, cost_out: '0' }).pct).toBeNull();
  });

  it('signs a loss with U+2212 in the amount and the pct', () => {
    const r = row(REALIZED_FIXTURE[3]);
    expect(r.amount).toBe('−4,897');
    expect(r.amountClass).toBe('down');
    expect(r.pct).toBe('−7.3%');
  });

  it('guards a foreign sell of quantity 0: avg —', () => {
    expect(row({ ...AAPL, quantity: 0 }).meta).toContain('均價 — → 賣出 212.30 USD');
  });

  it('omits the 匯率 without a positive native gross', () => {
    for (const native_proceeds_gross of [null, '0']) {
      expect(row({ ...AAPL, native_proceeds_gross }).detail.map(d => d.label)).not.toContain('匯率');
    }
  });

  it('shows a realized amount that rounds to zero as 0 without a trend class', () => {
    const r = row({ ...HONHAI, realized_pnl: '-0.4' });
    expect(r.amount).toBe('0');
    expect(r.amountClass).toBe('');
  });

  it('falls back to the symbol without a name and drops an empty broker', () => {
    const r = realizedRow(HONHAI, null, '');
    expect(r.name).toBe('2317');
    expect(r.meta).toBe('10/02 · 1,000 股 · 均價 160.73 → 賣出 175.00');
  });

  it('carries the 融券 and inventory warnings', () => {
    expect(row({ ...HONHAI, position_side: 'SHORT' }).warnings).toEqual([SHORT_WARNING]);
    expect(row({ ...HONHAI, note: 'no_long_inventory' }).warnings).toEqual(['LONG 賣出無現股庫存，成本以 0 計算']);
    expect(row({ ...HONHAI, position_side: 'SHORT', note: 'no_short_inventory' }).warnings).toEqual([
      SHORT_WARNING,
      'SHORT 回補無未平倉空單，視為純成本',
    ]);
  });
});

describe('realizedRows', () => {
  it('names each row and keeps duplicate sells apart by key', () => {
    const rows = realizedRows([HONHAI, HONHAI, TSMC], e => (e.symbol === '2317' ? '鴻海精密' : null), e => brokerLabel(e.broker));
    expect(rows.map(r => r.name)).toEqual(['鴻海精密', '鴻海精密', '2330']);
    expect(new Set(rows.map(r => r.key)).size).toBe(3);
  });
});
