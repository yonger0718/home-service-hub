import { Dividend, RecalcStatus } from '../../../models/portfolio.model';
import { ImportAttempt } from '../../portfolio/import/import-math';
import { dividendBatch, dividendsBadge, dividendsRow, importFailureTask, recalcFailed, splitPendingDividends } from './portfolio';

const TODAY = '2026-10-24';

function div(id: number, symbol: string, overrides: Partial<Dividend> = {}): Dividend {
  return { id, symbol, amount: 100 + id, ex_dividend_date: '2026-09-01', receipt_status: 'pending', ...overrides };
}

function attempt(overrides: Partial<ImportAttempt> = {}): ImportAttempt {
  return { at: '2026-10-24T09:05:00', file: '測試匯入.csv', type: 'broker', ok: false, ...overrides };
}

describe('dividend builders', () => {
  it('a payment_date carrying a time is compared by its date part: today counts as due', () => {
    const { due, waiting } = splitPendingDividends([div(1, '9901', { payment_date: '2026-10-24T00:00:00' })], TODAY);
    expect(due.map(d => d.id)).toEqual([1]);
    expect(waiting).toEqual([]);
  });

  it('the batch fingerprint orders ids as numbers (2 before 10)', () => {
    const { due } = splitPendingDividends(
      [div(10, '9901', { payment_date: '2026-10-20' }), div(2, '9902', { payment_date: '2026-10-21' })],
      TODAY,
    );
    expect(dividendBatch(due)?.fingerprint).toBe('2,10');
  });

  it('a failed quotes run does not hide a failed device import', () => {
    expect(importFailureTask({ kind: 'quotes', state: 'failed' }, attempt())).toMatchObject({ title: '最近一次匯入失敗' });
  });

  it('two pending dividends past their payment date form one 要你確認 task linking to 股利 待收', () => {
    const { due, waiting } = splitPendingDividends(
      [div(2, '9902', { payment_date: '2026-10-22' }), div(1, '9901', { payment_date: '2026-10-20' })],
      TODAY,
    );
    expect(waiting).toEqual([]);
    expect(dividendBatch(due)).toMatchObject({
      id: 'div:batch', source: 'portfolio', from: 'dividends', tier: 'confirm', icon: 'pi-percentage',
      title: '股利待確認收款 · 2 筆', when: '最早 10/20 發放', meta: '9901、9902', amount: null, progress: null,
      primary: { label: '查看', route: '/portfolio/dividends', queryParams: { status: 'pending' } },
      secondary: [], sortKey: '2026-10-20', fingerprint: '1,2',
    });
    expect(dividendsBadge(due)).toEqual({ dot: true, tone: 'primary', label: '2 筆待確認收款' });
  });

  it.each([
    ['2026-10-24', 'due'],
    ['2026-10-25', 'waiting'],
    [null, 'waiting'],
    [undefined, 'waiting'],
  ])('pending with payment_date %s is %s', (payment_date, where) => {
    const row = div(1, '9901', { payment_date });
    const { due, waiting } = splitPendingDividends([row], TODAY);
    expect(where === 'due' ? due : waiting).toEqual([row]);
    expect(where === 'due' ? waiting : due).toEqual([]);
  });

  it.each(['confirmed', 'legacy_unknown', 'unresolved', undefined] as const)('receipt_status %s is in neither list', (receipt_status) => {
    const { due, waiting } = splitPendingDividends([div(1, '9901', { receipt_status, payment_date: '2026-10-01' })], TODAY);
    expect(due).toEqual([]);
    expect(waiting).toEqual([]);
  });

  it('four symbols: meta 9901、9902、9903 等 1 檔; a symbol twice is listed once', () => {
    const rows = [
      div(1, '9901', { payment_date: '2026-10-01' }), div(2, '9902', { payment_date: '2026-10-02' }),
      div(3, '9903', { payment_date: '2026-10-03' }), div(4, '9904', { payment_date: '2026-10-04' }),
    ];
    expect(dividendBatch(rows)?.meta).toBe('9901、9902、9903 等 1 檔');
    const twice = [div(1, '9901', { payment_date: '2026-10-01' }), div(2, '9901', { payment_date: '2026-10-02' })];
    expect(dividendBatch(twice)).toMatchObject({ title: '股利待確認收款 · 2 筆', meta: '9901' });
  });

  it('nothing due: no task, no badge', () => {
    expect(dividendBatch([])).toBeNull();
    expect(dividendsBadge([])).toBeNull();
  });

  it('dividendsRow: 股利待發放 · 3 筆, dated rows first then 日期未定, count 3, source portfolio', () => {
    const row = dividendsRow([div(3, '9903'), div(2, '9902', { payment_date: '2026-11-05' }), div(1, '9901', { payment_date: '2026-10-30' })]);
    expect(row).toEqual({
      id: 'dividends', source: 'portfolio', title: '股利待發放 · 3 筆',
      meta: '9901 10/30 · 9902 11/05 · 9903 日期未定', count: 3,
      link: { label: '查看', route: '/portfolio/dividends', queryParams: { status: 'pending' } },
    });
    expect(dividendsRow([])).toBeNull();
  });

  it('dividendsRow with five rows ends in 等 2 筆', () => {
    const rows = [1, 2, 3, 4, 5].map(n => div(n, `990${n}`, { payment_date: `2026-11-0${n}` }));
    expect(dividendsRow(rows)?.meta).toBe('9901 11/01 · 9902 11/02 · 9903 11/03 · 等 2 筆');
  });
});

describe('import failure', () => {
  const failed: RecalcStatus = { kind: 'import', state: 'failed', started_at: '2026-10-24T08:00:00' };

  it('a failed recalc chain: 最近一次匯入的重算失敗, id import:fail:<started_at>, tier system', () => {
    expect(recalcFailed(failed)).toBe(true);
    expect(importFailureTask(failed, undefined)).toMatchObject({
      id: 'import:fail:2026-10-24T08:00:00', source: 'portfolio', from: 'imports', tier: 'system', icon: 'pi-upload',
      title: '最近一次匯入的重算失敗', when: '10/24 08:00', meta: null, amount: null, progress: null,
      primary: { label: '查看', route: '/portfolio/import' }, secondary: [],
      sortKey: '2026-10-24T08:00:00', fingerprint: '2026-10-24T08:00:00',
    });
  });

  it('a recalc without started_at keys on "recalc"', () => {
    expect(importFailureTask({ state: 'failed' }, undefined)?.id).toBe('import:fail:recalc');
  });

  it('a failed quotes run, or completed / partial / running / idle, is no failure', () => {
    const states = ['completed', 'partial', 'running', 'idle'] as const;
    for (const state of states) {
      expect(recalcFailed({ kind: 'import', state })).toBe(false);
      expect(importFailureTask({ kind: 'import', state }, undefined)).toBeNull();
    }
    expect(recalcFailed({ kind: 'quotes', state: 'failed' })).toBe(false);
    expect(importFailureTask({ kind: 'quotes', state: 'failed' }, undefined)).toBeNull();
  });

  it("the device's newest attempt failed: 最近一次匯入失敗, meta 測試匯入.csv, id import:fail:<at>", () => {
    expect(importFailureTask(null, attempt())).toMatchObject({
      id: 'import:fail:2026-10-24T09:05:00', title: '最近一次匯入失敗', when: '10/24 09:05', meta: '測試匯入.csv',
      tier: 'system', from: 'imports', sortKey: '2026-10-24T09:05:00', fingerprint: '2026-10-24T09:05:00',
    });
  });

  it("both: one task, the recalc's", () => {
    expect(importFailureTask(failed, attempt())).toMatchObject({ id: 'import:fail:2026-10-24T08:00:00', title: '最近一次匯入的重算失敗' });
  });

  it('neither (null recalc, undefined or ok attempt): null', () => {
    expect(importFailureTask(null, undefined)).toBeNull();
    expect(importFailureTask(null, attempt({ ok: true }))).toBeNull();
    expect(recalcFailed(null)).toBe(false);
  });
});
