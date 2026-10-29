import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Confirmation, ConfirmationService, MessageService } from 'primeng/api';
import { BehaviorSubject, Observable, of, Subject, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ImportHistoryService } from '../../../services/import-history.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { BrokerCsvImportResult, ImportResult, ImportRow } from '../../../models/portfolio.model';
import { PortfolioImportComponent } from './import';

const preview: ImportResult = { parsed: 1, created: 1, skipped_duplicates: 0, dry_run: true, errors: [], created_ids: [], rows: [] };
const brokerPreview: BrokerCsvImportResult = {
  detected_broker: 'firstrade', dry_run: true,
  transactions: [{ row_index: 2, fingerprint: 'a', payload: { symbol: 'AAPL' } }],
  cash_flows: [{ row_index: 3, fingerprint: 'b', payload: { amount: '10' } }],
  counts: { created: 2, skipped: 0, rejected: 0 }, errors: [],
};

const UNRESOLVED = { name: '台灣50正2', occurrences: 3, sample_dates: ['10/21', '10/14', '09/30'] };
const csvRow = (row_index: number, payload: Record<string, string>, status?: ImportRow['status']): ImportRow =>
  ({ row_index, fingerprint: `fp-${row_index}`, payload, ...(status ? { status } : {}) });
/** Wire-shaped generic dry-run (no row status): 41 parsed, 3 duplicates, 1 error. */
const genericPreview: ImportResult = {
  ...preview, parsed: 41, created: 0, skipped_duplicates: 3, would_insert: 0, would_skip_duplicate: 0, csv_format: 'generic',
  errors: [{ row_index: 4, message: '價格欄位為空' }],
  unresolved_names: [UNRESOLVED],
  rows: [
    csvRow(2, { trade_date: '2026-10-20', name: '台積電', symbol: '2330', type: 'BUY', quantity: '100', price: '1075.00' }),
    csvRow(3, { trade_date: '2026-10-21', name: '台灣50正2', type: 'BUY', quantity: '1000', price: '285.30' }),
    csvRow(4, { trade_date: '2026-10-08', name: '元大高股息', symbol: '0056', type: 'SELL', quantity: '2000', price: '' }),
  ],
};
/** Nested-payload broker response as the real API returns it (`/imports/csv` dry-run). */
const brokerNested: BrokerCsvImportResult = {
  detected_broker: 'FIRSTRADE', dry_run: true,
  transactions: [
    { row_index: 2, fingerprint: 't2', status: 'new', payload: { symbol: 'MSFT', quantity: '2', price: '321.50', type: 'BUY', trade_date: '2026-06-05', broker: 'FIRSTRADE' } },
    { row_index: 4, fingerprint: 't4', status: 'duplicate', payload: { symbol: 'AAPL', quantity: '1', price: '190.50', type: 'SELL', trade_date: '2026-06-01', broker: 'FIRSTRADE' } },
  ],
  cash_flows: [{ row_index: 3, fingerprint: 'c3', status: 'new', payload: { date: '2026-06-05', broker: 'FIRSTRADE', cash_flow_type: 'deposit', amount: '2500.00', currency: 'USD' } }],
  counts: { created: 2, skipped: 1, rejected: 0 }, errors: [],
};

describe('Portfolio import controls', () => {
  const service = { uploadCsv: vi.fn(), uploadBrokerCsv: vi.fn(), getRecalcStatus: vi.fn(), triggerRecalc: vi.fn(), verifyOverrideSymbol: vi.fn() };
  let query$: BehaviorSubject<ParamMap>;
  let navigate: ReturnType<typeof vi.spyOn>;

  function setup(query: Record<string, string> = {}) {
    query$ = new BehaviorSubject(convertToParamMap(query));
    TestBed.configureTestingModule({
      imports: [PortfolioImportComponent],
      providers: [
        provideRouter([]),
        { provide: PortfolioService, useValue: service },
        { provide: ActivatedRoute, useValue: { queryParamMap: query$, get snapshot() { return { queryParamMap: query$.value }; } } },
      ],
    });
    navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(PortfolioImportComponent);
    fixture.detectChanges();
    return fixture;
  }

  const el = (fixture: { nativeElement: HTMLElement }) => fixture.nativeElement as HTMLElement;
  const text = (fixture: { nativeElement: HTMLElement }) => el(fixture).textContent ?? '';
  const typeButton = (fixture: { nativeElement: HTMLElement }, label: string) =>
    Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('[aria-label="匯入類型"] button'))
      .find(b => b.textContent?.trim() === label)!;
  const selectedType = (fixture: { nativeElement: HTMLElement }) =>
    el(fixture).querySelector('[role="radiogroup"][aria-label="匯入類型"] [aria-checked="true"]')?.textContent?.trim();
  const headerCheckbox = (fixture: { nativeElement: HTMLElement }) =>
    Array.from(el(fixture).querySelectorAll('label')).find(l => l.textContent?.includes('首列為欄位名稱'));
  const headerSwitch = (fixture: { nativeElement: HTMLElement }) =>
    el(fixture).querySelector<HTMLButtonElement>('label.header-check [role="switch"]');
  const button = (fixture: { nativeElement: HTMLElement }, label: string) =>
    Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.trim() === label);
  const activeStep = (fixture: { nativeElement: HTMLElement }) =>
    el(fixture).querySelector('ol[aria-label="匯入步驟"] li[aria-current="step"]')?.textContent ?? '';
  /** The 匯入並重算 section (step 3) holding the recalc panel. */
  const recalcCard = (fixture: { nativeElement: HTMLElement }) => el(fixture).querySelector<HTMLElement>('app-bento.recalc-card');
  const recalcText = (fixture: { nativeElement: HTMLElement }) => recalcCard(fixture)?.textContent ?? '';

  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    service.uploadCsv.mockReturnValue(of(preview));
    service.uploadBrokerCsv.mockReturnValue(of(brokerPreview));
    service.getRecalcStatus.mockReturnValue(of({ state: 'idle' }));
  });
  afterEach(() => {
    TestBed.inject(LayoutModeService).set(null);
    localStorage.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('switches the selected file to dividend parsing with fresh overrides and displays dividend columns', () => {
    const fixture = setup();
    const component = fixture.componentInstance;
    const file = new File(['symbol,amount,ex_dividend_date\n2330,25,2026-09-04'], 'dividend.csv');
    component.onSelect({ files: [file] });
    component.setOverride('old name', '9999');
    fixture.detectChanges();
    typeButton(fixture, '股利').click();
    fixture.detectChanges();
    expect(service.uploadCsv).toHaveBeenLastCalledWith('dividends', file, true, true, {}, []);
    expect(component.nameOverrides()).toEqual({});
    expect(component.type()).toBe('dividends');
    expect(navigate).toHaveBeenCalledWith([], { queryParams: { type: 'dividends' }, replaceUrl: true });
  });

  it('invalidates remote verification and confirmation when an override code changes', () => {
    const component = setup().componentInstance;
    component.result.set({ ...preview, override_validations: [{ name: 'company', code: '2330', status: 'verified' }] });
    component.toggleConfirm('company', true);
    component.setOverride('company', '0050');
    expect(component.validationFor('company')).toBeNull();
    expect(component.confirmedOverrides().has('company')).toBe(false);
  });

  it('prevents duplicate recalc submissions while a request is pending', () => {
    const response = new Subject<unknown>();
    service.triggerRecalc.mockReturnValue(response);
    const component = setup().componentInstance;
    component.retryRecalc(); component.retryRecalc();
    expect(service.triggerRecalc).toHaveBeenCalledTimes(1);
    response.next({}); response.complete();
    expect(component.recalcSubmitting()).toBe(false);
  });

  it('shows deferred historical amount and dates alongside quote success', () => {
    service.getRecalcStatus.mockReturnValue(of({
      state: 'partial', kind: 'import', quote_refresh: { state: 'completed', kind: 'quotes' },
      steps: [{ name: 'dividend_auto_record', status: 'partial', detail: {
        source_errors: [{ source: 'TWT49U', symbol: '9802', year: 2026, reason: 'detail unavailable' }],
        deferred_events: [{ symbol: '9802', ex_date: '2026-09-10', cash_dividend_per_share: '3.100000',
          payment_date: null, reason: 'retrieved; recording deferred—payment accounting unsupported' }],
      } }],
    }));
    const fixture = setup();
    expect(recalcCard(fixture)?.querySelector('h2')?.textContent?.trim()).toBe('匯入並重算');
    const content = recalcText(fixture);
    expect(content).toContain('匯入重算：部分完成');
    expect(content).toContain('報價更新（不含股利）：完成');
    expect(content).toContain('歷史股利核對：部分完成');
    expect(content).toContain('9802');
    expect(content).toContain('2026-09-10');
    expect(content).toContain('3.100000');
    expect(content).toContain('發放日：未知');
    expect(content).toContain('recording deferred');
    expect(content).toContain('detail unavailable');
    expect(content).not.toContain('2026-10-15');
  });

  it('does not label a quote-only run as completed import reconciliation', () => {
    service.getRecalcStatus.mockReturnValue(of({ state: 'completed', kind: 'quotes', quote_refresh: { state: 'completed' } }));
    expect(recalcText(setup())).toContain('匯入重算：閒置');
  });

  it('an unknown future step renders its raw name with the status label, raw values in title', () => {
    service.getRecalcStatus.mockReturnValue(of({
      kind: 'import', state: 'partial', quote_refresh: { state: 'completed', steps: [{ name: 'future_quote_step', status: 'skipped' }] },
      steps: [{ name: 'future_step', status: 'ok' }, { name: 'networth_backfill', status: 'failed', error: 'boom' }],
    }));
    const fixture = setup();
    const content = recalcText(fixture);
    expect(content).toContain('future_step：完成');
    expect(content).toContain('淨值回補：失敗 boom');
    expect(content).toContain('future_quote_step：略過');
    const card = recalcCard(fixture)!;
    expect(card.querySelector('.recalc-state')?.getAttribute('title')).toBe('partial');
    expect(card.querySelector('.recalc-step span[title="networth_backfill"]')?.textContent?.trim()).toBe('淨值回補');
  });

  it('an unknown recalc state renders raw without throwing', () => {
    const fixture = setup();
    const component = fixture.componentInstance;
    component.committed.set(true);
    component.recalcStatus.set({ kind: 'import', state: 'weird' as never, steps: [{ name: 'future_step', status: 'odd' as never }] });
    fixture.detectChanges();
    expect(recalcText(fixture)).toContain('匯入重算：weird');
    expect(recalcText(fixture)).toContain('future_step：odd');
    expect(recalcCard(fixture)?.querySelector('.recalc-state')?.getAttribute('title')).toBe('weird');
  });

  it('updates running quote status without losing a partial import', () => {
    vi.useFakeTimers();
    service.getRecalcStatus.mockReturnValue(of({ state: 'partial', kind: 'import', quote_refresh: { state: 'running' } }));
    const fixture = setup();
    service.getRecalcStatus.mockReturnValue(of({ state: 'partial', kind: 'import', quote_refresh: { state: 'completed' } }));
    vi.advanceTimersByTime(5000);
    fixture.detectChanges();
    expect(recalcText(fixture)).toContain('匯入重算：部分完成');
    expect(recalcText(fixture)).toContain('報價更新（不含股利）：完成');
    fixture.destroy();
  });

  describe('step 3: 匯入並重算', () => {
    for (const state of ['running', 'failed', 'partial'] as const) {
      it(`on load with a ${state} import recalc the panel shows without a file`, () => {
        vi.useFakeTimers();
        service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state }));
        const fixture = setup();
        expect(fixture.componentInstance.file()).toBeNull();
        expect(fixture.componentInstance.step()).toBe(1);
        expect(recalcText(fixture)).toContain(`匯入重算：${({ running: '執行中', failed: '失敗', partial: '部分完成' })[state]}`);
        fixture.destroy();
      });
    }

    it('after a commit the panel renders in 匯入並重算 with steps, deferred dividends, quote status and the 重算資料 guard', () => {
      vi.useFakeTimers();
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(recalcCard(fixture)).toBeNull();
      service.uploadCsv.mockReturnValue(of({ ...preview, dry_run: false, recalc_scheduled: true }));
      service.getRecalcStatus.mockReturnValue(of({
        kind: 'import', state: 'running', started_at: '2026-10-23T10:00:00Z', quote_refresh: { state: 'running', steps: [{ name: 'networth_backfill', status: 'ok' }] },
        steps: [
          { name: 'symbol_map_backfill', status: 'ok' },
          { name: 'dividend_auto_record', status: 'partial', detail: {
            deferred_events: [{ symbol: '2330', ex_date: '2026-09-18', cash_dividend_per_share: '5.0', payment_date: '2026-10-09', reason: 'deferred' }],
          } },
        ],
      }));
      fixture.componentInstance.commit();
      fixture.detectChanges();
      expect(activeStep(fixture)).toContain('匯入並重算');
      const card = recalcCard(fixture)!;
      expect(card.querySelector('h2')?.textContent?.trim()).toBe('匯入並重算');
      const content = recalcText(fixture);
      expect(content).toContain('匯入重算：執行中');
      expect(content).toContain('最近匯入重算：2026-10-23T10:00:00Z');
      expect(content).toContain('代號對照補齊：完成');
      expect(content).toContain('歷史股利核對：部分完成');
      expect(card.querySelector('.deferred-dividend')?.textContent).toContain('2330');
      expect(content).toContain('發放日：2026-10-09');
      expect(content).toContain('報價更新（不含股利）：執行中');
      expect(content).toContain('淨值回補：完成');
      const rerun = Array.from(card.querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.trim() === '重算資料')!;
      expect(rerun.disabled).toBe(true);
      // The side cards come after the 匯入並重算 section in sheet / phone.
      const side = el(fixture).querySelector('aside[aria-label="匯入後會發生什麼"]')!;
      expect(card.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      fixture.destroy();
    });

    it('a commit landing after destroy records history but never starts polling', () => {
      vi.useFakeTimers();
      const commit$ = new Subject<ImportResult>();
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'late.csv')] });
      service.uploadCsv.mockReturnValue(commit$);
      fixture.componentInstance.commit();
      fixture.destroy();
      service.getRecalcStatus.mockClear();
      service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'running' }));
      commit$.next({ ...preview, dry_run: false, recalc_scheduled: true }); commit$.complete();
      vi.advanceTimersByTime(60_000);
      expect(service.getRecalcStatus).not.toHaveBeenCalled();
      expect(TestBed.inject(ImportHistoryService).attempts()[0]?.file).toBe('late.csv');
    });

    it('a partial recalc toast lists the failed steps by label', () => {
      vi.useFakeTimers();
      service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'running' }));
      const fixture = setup();
      const add = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      service.getRecalcStatus.mockReturnValue(of({
        kind: 'import', state: 'partial', steps: [
          { name: 'symbol_map_backfill', status: 'failed' },
          { name: 'dividend_auto_record', status: 'ok' },
          { name: 'networth_backfill', status: 'partial' },
        ],
      }));
      vi.advanceTimersByTime(5000);
      expect(add).toHaveBeenCalledWith(expect.objectContaining({
        severity: 'warn', summary: '資料重算尚未完成', detail: '失敗步驟：代號對照補齊, 淨值回補。可點擊「重試」',
      }));
      fixture.destroy();
    });

    it('a failed 重算資料 toast reads the shared body message', () => {
      service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'failed', steps: [{ name: 'dividend_auto_record', status: 'failed', error: 'x' }] }));
      service.triggerRecalc.mockReturnValue(throwError(() => ({ status: 409, message: 'Http failure', error: { code: 409, message: 'recalc in progress', trace_id: null } })));
      const fixture = setup();
      const add = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      fixture.componentInstance.retryRecalc();
      expect(add).toHaveBeenCalledWith(expect.objectContaining({ summary: '重算觸發失敗', detail: 'recalc in progress' }));
    });

    it('重算資料 is enabled once the recalc settles and triggers one recalc', () => {
      service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'failed', steps: [{ name: 'dividend_auto_record', status: 'failed', error: 'x' }] }));
      service.triggerRecalc.mockReturnValue(new Subject());
      const fixture = setup();
      const rerun = Array.from(recalcCard(fixture)!.querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.trim() === '重算資料')!;
      expect(rerun.disabled).toBe(false);
      rerun.click(); rerun.click();
      expect(service.triggerRecalc).toHaveBeenCalledTimes(1);
    });
  });

  describe('type from the query', () => {
    it('?type=broker selects 券商對帳單, hides the header checkbox and shows the broker hint', () => {
      const fixture = setup({ type: 'broker' });
      expect(fixture.componentInstance.type()).toBe('broker');
      expect(selectedType(fixture)).toBe('券商對帳單');
      expect(headerCheckbox(fixture)).toBeUndefined();
      expect(text(fixture)).toContain('券商對帳單（交易＋現金流）');
      expect(text(fixture)).not.toContain('支援自動偵測：國泰證券');
      expect(navigate).not.toHaveBeenCalled();
    });

    it('?type=dividends selects 股利 with the generic hint and the header checkbox', () => {
      const fixture = setup({ type: 'dividends' });
      expect(fixture.componentInstance.type()).toBe('dividends');
      expect(selectedType(fixture)).toBe('股利');
      expect(headerCheckbox(fixture)).toBeDefined();
      expect(text(fixture)).toContain('通用欄位格式（日期、名稱、買賣、股數、價格）');
      expect(text(fixture)).toContain('支援自動偵測：國泰證券');
    });

    it('an unknown ?type= falls back to 交易', () => {
      const fixture = setup({ type: 'x' });
      expect(fixture.componentInstance.type()).toBe('transactions');
      expect(selectedType(fixture)).toBe('交易');
    });

    it('a later query change switches the type without reloading or writing the param back', () => {
      const fixture = setup();
      const component = fixture.componentInstance;
      query$.next(convertToParamMap({ type: 'dividends' }));
      fixture.detectChanges();
      expect(component.type()).toBe('dividends');
      expect(selectedType(fixture)).toBe('股利');
      query$.next(convertToParamMap({}));
      fixture.detectChanges();
      expect(component.type()).toBe('transactions');
      expect(navigate).not.toHaveBeenCalled();
    });

    it('a query change with a file re-previews on the new endpoint', () => {
      const fixture = setup();
      const file = new File(['x'], 'a.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      query$.next(convertToParamMap({ type: 'broker' }));
      expect(service.uploadBrokerCsv).toHaveBeenLastCalledWith(file, true);
      expect(fixture.componentInstance.file()).toBe(file);
    });
  });

  describe('commit and type changes', () => {
    it('commit() holds step 3 while the request is pending and after it lands', () => {
      const commit$ = new Subject<ImportResult>();
      const fixture = setup();
      const component = fixture.componentInstance;
      component.onSelect({ files: [new File(['x'], 'a.csv')] });
      service.uploadCsv.mockReturnValue(commit$);
      component.commit();
      fixture.detectChanges();
      expect(service.uploadCsv).toHaveBeenLastCalledWith('transactions', expect.any(File), false, true, {}, []);
      expect(component.committing()).toBe(true);
      expect(component.step()).toBe(3);
      expect(activeStep(fixture)).toContain('匯入並重算');
      commit$.next({ ...preview, dry_run: false }); commit$.complete();
      fixture.detectChanges();
      expect(component.committing()).toBe(false);
      expect(component.committed()).toBe(true);
      expect(component.step()).toBe(3);
    });

    it('a failed commit leaves committing without marking it committed', () => {
      const fixture = setup({ type: 'broker' });
      const component = fixture.componentInstance;
      component.onSelect({ files: [new File(['x'], 'a.csv')] });
      service.uploadBrokerCsv.mockReturnValue(throwError(() => ({ status: 500, error: { detail: 'boom' } })));
      component.commit();
      expect(service.uploadBrokerCsv).toHaveBeenLastCalledWith(expect.any(File), false);
      expect(component.committing()).toBe(false);
      expect(component.committed()).toBe(false);
      expect(component.step()).toBe(2);
    });

    it('a ?type= change during a dry-run cancels the stale preview and re-previews on the new endpoint', () => {
      const stale$ = new Subject<ImportResult>();
      service.uploadCsv.mockReturnValue(stale$);
      const fixture = setup();
      const component = fixture.componentInstance;
      const file = new File(['x'], 'a.csv');
      component.onSelect({ files: [file] });
      expect(component.busy()).toBe(true);
      query$.next(convertToParamMap({ type: 'broker' }));
      stale$.next(preview);
      expect(component.result()).toBeNull();
      expect(service.uploadBrokerCsv).toHaveBeenCalledWith(file, true);
      expect(component.brokerResult()).toEqual(brokerPreview);
      expect(component.type()).toBe('broker');
    });

    it('a ?type= change ignored during a commit writes the current type back once the commit settles', () => {
      const commit$ = new Subject<ImportResult>();
      const fixture = setup();
      const component = fixture.componentInstance;
      component.onSelect({ files: [new File(['x'], 'a.csv')] });
      service.uploadCsv.mockReturnValue(commit$);
      component.commit();
      query$.next(convertToParamMap({ type: 'dividends' }));
      expect(component.type()).toBe('transactions');
      expect(navigate).not.toHaveBeenCalled();
      commit$.next({ ...preview, dry_run: false }); commit$.complete();
      expect(component.type()).toBe('transactions');
      expect(component.committed()).toBe(true);
      expect(navigate).toHaveBeenCalledWith([], { queryParams: { type: 'transactions' }, replaceUrl: true });
    });
  });

  describe('steps strip', () => {
    it('shows step 1 before a file, 2 after a dry-run result and 3 while committing or committed', () => {
      const fixture = setup();
      const component = fixture.componentInstance;
      const steps = el(fixture).querySelectorAll('ol[aria-label="匯入步驟"] li');
      expect(Array.from(steps).map(s => s.textContent?.replace(/\s+/g, ''))).toEqual([
        expect.stringContaining('選擇檔案'), expect.stringContaining('預覽與確認'), expect.stringContaining('匯入並重算寫入後自動重算'),
      ]);
      expect(activeStep(fixture)).toContain('選擇檔案');
      component.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(component.step()).toBe(2);
      expect(activeStep(fixture)).toContain('預覽與確認');
      expect(el(fixture).querySelector('ol[aria-label="匯入步驟"] li.done')?.textContent).toContain('選擇檔案');
      component.committing.set(true);
      fixture.detectChanges();
      expect(activeStep(fixture)).toContain('匯入並重算');
      expect(el(fixture).querySelectorAll('ol[aria-label="匯入步驟"] li.done').length).toBe(2);
      component.committing.set(false);
      component.committed.set(true);
      fixture.detectChanges();
      expect(component.step()).toBe(3);
    });
  });

  describe('檔案 card', () => {
    it('a 交易 file posts a dry-run with the header flag and shows the chip with the Cathay format', () => {
      service.uploadCsv.mockReturnValue(of({ ...preview, parsed: 42, csv_format: 'cathay' }));
      const fixture = setup();
      const file = new File(['x'], 'cathay.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      expect(service.uploadCsv).toHaveBeenCalledWith('transactions', file, true, true, {}, []);
      expect(service.uploadBrokerCsv).not.toHaveBeenCalled();
      expect(el(fixture).querySelector('app-file-chip')?.textContent).toContain('cathay.csv');
      expect(text(fixture)).toContain('42 列 · 偵測格式：國泰證券');
      expect(el(fixture).querySelector('select')).toBeNull();
    });

    it('a 券商對帳單 file posts uploadBrokerCsv without overrides and shows the detected broker', () => {
      const fixture = setup({ type: 'broker' });
      const file = new File(['x'], 'ft.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      expect(service.uploadBrokerCsv).toHaveBeenCalledWith(file, true);
      expect(service.uploadCsv).not.toHaveBeenCalled();
      expect(fixture.componentInstance.brokerResult()).toEqual(brokerPreview);
      expect(text(fixture)).toContain('2 列 · 偵測格式：FIRSTRADE');
      expect(text(fixture)).not.toContain('將以通用手動 CSV 解析');
    });

    it('an unknown broker header shows manual with the generic-CSV warning', () => {
      service.uploadBrokerCsv.mockReturnValue(of({ ...brokerPreview, detected_broker: null }));
      const fixture = setup({ type: 'broker' });
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'm.csv')] });
      fixture.detectChanges();
      expect(text(fixture)).toContain('偵測格式：manual');
      expect(text(fixture)).toContain('將以通用手動 CSV 解析');
    });

    it('switching type with a file re-previews on the new endpoint and clears overrides', () => {
      const fixture = setup();
      const component = fixture.componentInstance;
      const file = new File(['x'], 'a.csv');
      component.onSelect({ files: [file] });
      component.setOverride('台積電', '2330');
      fixture.detectChanges();
      typeButton(fixture, '券商對帳單').click();
      fixture.detectChanges();
      expect(service.uploadBrokerCsv).toHaveBeenLastCalledWith(file, true);
      expect(component.nameOverrides()).toEqual({});
      expect(component.result()).toBeNull();
      expect(component.brokerResult()).toEqual(brokerPreview);
      expect(navigate).toHaveBeenCalledWith([], { queryParams: { type: 'broker' }, replaceUrl: true });
      typeButton(fixture, '交易').click();
      fixture.detectChanges();
      expect(service.uploadCsv).toHaveBeenLastCalledWith('transactions', file, true, true, {}, []);
      expect(component.brokerResult()).toBeNull();
    });

    it('unchecking 首列為欄位名稱 re-previews with has_header=false', () => {
      const fixture = setup();
      const file = new File(['x'], 'a.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      headerSwitch(fixture)!.click();
      fixture.detectChanges();
      expect(service.uploadCsv).toHaveBeenLastCalledWith('transactions', file, true, false, {}, []);
    });

    it('renders 首列為欄位名稱 as a switch that is on by default and disabled while busy', () => {
      const pending$ = new Subject<ImportResult>();
      service.uploadCsv.mockReturnValue(pending$);
      const fixture = setup();
      expect(el(fixture).querySelector('label.header-check [role="switch"][aria-checked="true"]')).not.toBeNull();
      expect(headerSwitch(fixture)!.disabled).toBe(false);
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] }); // preview in flight, never flushed
      fixture.detectChanges();
      expect(fixture.componentInstance.busy()).toBe(true);
      expect(headerSwitch(fixture)!.disabled).toBe(true);
    });

    it('hides the switch for 券商對帳單', () => {
      const fixture = setup();
      typeButton(fixture, '券商對帳單').click();
      fixture.detectChanges();
      expect(el(fixture).querySelector('label.header-check')).toBeNull();
    });

    it('renders no footer before a file is chosen', () => {
      const fixture = setup();
      expect(el(fixture).querySelector('.imp-footer')).toBeNull();
      expect(button(fixture, '確認匯入')).toBeUndefined();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(el(fixture).querySelector('.imp-footer')).not.toBeNull();
    });

    it('更換檔案 opens the native file input', () => {
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      const input = el(fixture).querySelector<HTMLInputElement>('input[type="file"]')!;
      const click = vi.spyOn(input, 'click').mockImplementation(() => undefined);
      button(fixture, '更換檔案')!.click();
      expect(click).toHaveBeenCalledTimes(1);
    });

    it('移除檔案 clears the file, results and overrides', () => {
      const fixture = setup({ type: 'broker' });
      const component = fixture.componentInstance;
      component.onSelect({ files: [new File(['x'], 'a.csv')] });
      component.committed.set(true);
      fixture.detectChanges();
      el(fixture).querySelector<HTMLButtonElement>('button[aria-label="移除檔案"]')!.click();
      fixture.detectChanges();
      expect(component.file()).toBeNull();
      expect(component.brokerResult()).toBeNull();
      expect(component.result()).toBeNull();
      expect(component.committed()).toBe(false);
      expect(el(fixture).querySelector('app-file-chip')).toBeNull();
      expect(el(fixture).querySelector('.dropzone')).not.toBeNull();
      expect(component.step()).toBe(1);
    });

    it('a broker HTTP error keeps the file and surfaces the row errors', () => {
      service.uploadBrokerCsv.mockReturnValue(throwError(() => ({ status: 422, error: { errors: [{ row_index: 4, reason: 'bad date' }] } })));
      const fixture = setup({ type: 'broker' });
      const file = new File(['x'], 'a.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      expect(fixture.componentInstance.file()).toBe(file);
      expect(fixture.componentInstance.uploadErrors()).toEqual([{ row_index: 4, reason: 'bad date' }]);
      expect(text(fixture)).toContain('bad date');
    });
  });

  describe('預覽 / 需要修正的列 / 確認股票代號 / footer', () => {
    const card = (fixture: { nativeElement: HTMLElement }, cls: string) => el(fixture).querySelector<HTMLElement>(`app-bento.${cls}`);
    const norm = (t: string | null | undefined) => (t ?? '').replace(/\s+/g, ' ').trim();
    const cells = (tr: Element | null | undefined) => Array.from(tr?.querySelectorAll('td') ?? []).map(td => norm(td.textContent));
    const heads = (table: Element | null | undefined) => Array.from(table?.querySelectorAll('th') ?? []).map(th => norm(th.textContent));
    const commitButton = (fixture: { nativeElement: HTMLElement }) =>
      Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.imp-footer button')).find(b => norm(b.textContent).startsWith('確認匯入'))!;
    function withFile(result: ImportResult, query: Record<string, string> = {}) {
      service.uploadCsv.mockReturnValue(of(result));
      const fixture = setup(query);
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      return fixture;
    }
    function withBrokerFile(result: BrokerCsvImportResult) {
      service.uploadBrokerCsv.mockReturnValue(of(result));
      const fixture = setup({ type: 'broker' });
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'ft.csv')] });
      fixture.detectChanges();
      return fixture;
    }

    it('renders the 預覽 card with the wire-rule count tags, the 交易 grid and per-row results', () => {
      const fixture = withFile(genericPreview);
      const preview = card(fixture, 'preview-card')!;
      expect(norm(preview.querySelector('h2')?.textContent)).toBe('預覽');
      expect(Array.from(preview.querySelectorAll('.count-tags app-tag')).map(t => norm(t.textContent))).toEqual(['解析 41', '重複 3', '錯誤 1']);
      expect(heads(preview.querySelector('table'))).toEqual(['日期', '股票', '買賣', '股數', '價格', '結果']);
      const rows = Array.from(preview.querySelectorAll('tbody tr'));
      expect(rows.length).toBe(3);
      expect(cells(rows[0])).toEqual(['2026-10-20', '台積電2330', '買進', '100', '1075.00', '新增']);
      expect(norm(rows[0].querySelector('.c-stock small')?.textContent)).toBe('2330');
      expect(cells(rows[1])[1]).toBe('台灣50正2');
      expect(rows[1].querySelector('.c-stock small')).toBeNull();
      expect(norm(rows[0].querySelector('app-side-tag')?.textContent)).toBe('買進');
      expect(norm(rows[2].querySelector('app-side-tag')?.textContent)).toBe('賣出');
      expect(rows.map(r => norm(r.querySelector('td:last-child app-tag')?.textContent))).toEqual(['新增', '代號待確認', '錯誤']);
      expect(rows[1].querySelector('app-tag .tag-warning')).not.toBeNull();
      expect(rows[2].querySelector('app-tag .tag-danger')).not.toBeNull();
      // Without row statuses nothing is a duplicate, so nothing is dimmed (an error row is not).
      expect(preview.querySelector('.dimmed')).toBeNull();
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 38 筆');
    });

    it('股利 swaps 買賣 / 股數 / 價格 for 股利金額 / 入帳日', () => {
      const fixture = withFile({ ...preview, rows: [csvRow(2, { ex_dividend_date: '2026-09-04', symbol: '2330', amount: '25', received_date: '2026-10-09' })] }, { type: 'dividends' });
      const table = card(fixture, 'preview-card')!.querySelector('table');
      expect(heads(table)).toEqual(['日期', '股票', '股利金額', '入帳日', '結果']);
      expect(cells(table?.querySelector('tbody tr'))).toEqual(['2026-09-04', '2330', '25', '2026-10-09', '新增']);
    });

    it('a Cathay dry-run reads would_insert / would_skip_duplicate', () => {
      const fixture = withFile({ ...genericPreview, csv_format: 'cathay', skipped_duplicates: 0, would_insert: 38, would_skip_duplicate: 3 });
      expect(Array.from(card(fixture, 'preview-card')!.querySelectorAll('.count-tags app-tag')).map(t => norm(t.textContent)))
        .toEqual(['解析 41', '重複 3', '錯誤 1']);
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 38 筆');
    });

    function dryRunToast(query: Record<string, string>): string {
      const fixture = setup(query);
      const add = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      return String(add.mock.calls.find(([m]) => m.summary === '預覽完成')?.[0].detail);
    }

    it('a Cathay dry-run toast reads the preview counts, not created / skipped_duplicates', () => {
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, csv_format: 'cathay', created: 0, skipped_duplicates: 0, would_insert: 38, would_skip_duplicate: 3 }));
      const detail = dryRunToast({});
      expect(detail).toContain('可新增 38；重複 3；錯誤 1');
      expect(detail).not.toContain('可新增 0');
    });

    it('a broker dry-run toast counts the row statuses, not the wire counts', () => {
      service.uploadBrokerCsv.mockReturnValue(of({ ...brokerNested, counts: { created: 0, skipped: 0, rejected: 0 } }));
      expect(dryRunToast({ type: 'broker' })).toBe('可新增 2；重複 1；錯誤 0');
    });

    it('rows with status: duplicates are dimmed and counted, N is the number of new rows', () => {
      const fixture = withFile({
        ...genericPreview, skipped_duplicates: 9, unresolved_names: [],
        rows: [
          csvRow(2, { name: 'A', type: 'BUY' }, 'new'), csvRow(3, { name: 'B', type: 'BUY' }, 'new'),
          csvRow(4, { name: 'C', type: 'BUY' }, 'duplicate'), csvRow(5, { name: 'D', type: 'BUY' }, 'unresolved'), csvRow(6, { name: 'E', type: 'BUY' }, 'error'),
        ],
      });
      const preview = card(fixture, 'preview-card')!;
      expect(norm(preview.querySelectorAll('.count-tags app-tag')[1].textContent)).toBe('重複 1');
      const rows = Array.from(preview.querySelectorAll('tbody tr'));
      expect(rows.map(r => norm(r.querySelector('td:last-child')?.textContent))).toEqual(['新增', '新增', '重複 · 略過', '代號待確認', '錯誤']);
      expect(rows.map(r => r.classList.contains('dimmed'))).toEqual([false, false, true, false, false]);
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 2 筆');
    });

    it('a Cathay preview with rehash rows shows 更新 (not dimmed, with the hint), an 更新 tag, and N excludes them', () => {
      const fixture = withFile({
        ...genericPreview, csv_format: 'cathay', parsed: 4, skipped_duplicates: 0, would_insert: 1, would_skip_duplicate: 1, would_rehash: 2,
        errors: [], unresolved_names: [],
        rows: [
          csvRow(2, { name: 'A', type: 'BUY' }, 'new'), csvRow(3, { name: 'B', type: 'BUY' }, 'duplicate'),
          csvRow(4, { name: 'C', type: 'BUY' }, 'rehash'), csvRow(5, { name: 'D', type: 'SELL' }, 'rehash'),
        ],
      });
      const preview = card(fixture, 'preview-card')!;
      expect(Array.from(preview.querySelectorAll('.count-tags app-tag')).map(t => norm(t.textContent)))
        .toEqual(['解析 4', '重複 1', '更新 2', '錯誤 0']);
      const rows = Array.from(preview.querySelectorAll('tbody tr'));
      expect(rows.map(r => norm(r.querySelector('td:last-child app-tag')?.textContent))).toEqual(['新增', '重複 · 略過', '更新', '更新']);
      expect(rows.map(r => r.classList.contains('dimmed'))).toEqual([false, true, false, false]);
      const hint = '既有交易原地更新：指紋、多空、當沖標記與現金腳';
      expect(rows[2].querySelector('td:last-child app-tag')?.getAttribute('title')).toBe(hint);
      expect(norm(rows[2].querySelector('td:last-child .sr-only')?.textContent)).toContain(hint);
      expect(rows[0].querySelector('td:last-child app-tag')?.hasAttribute('title')).toBe(false);
      expect(rows[0].querySelector('td:last-child .sr-only')).toBeNull();
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 1 筆');
    });

    it('a preview without rehash rows shows no 更新 tag', () => {
      const fixture = withFile({ ...genericPreview, csv_format: 'cathay', skipped_duplicates: 0, would_insert: 38, would_skip_duplicate: 3, would_rehash: 0 });
      const tags = Array.from(card(fixture, 'preview-card')!.querySelectorAll('.count-tags app-tag')).map(t => norm(t.textContent));
      expect(tags).toEqual(['解析 41', '重複 3', '錯誤 1']);
      expect(tags.some(t => t.startsWith('更新'))).toBe(false);
    });

    it('券商 dry-run renders the transactions and 現金流 tables from the nested payload without a created count', () => {
      const fixture = withBrokerFile(brokerNested);
      const preview = card(fixture, 'preview-card')!;
      const tables = preview.querySelectorAll('table');
      expect(tables.length).toBe(2);
      expect(heads(tables[0])).toEqual(['日期', '股票', '買賣', '股數', '價格', '結果']);
      expect(heads(tables[1])).toEqual(['日期', '類型', '金額', '幣別']);
      const tx = tables[0].querySelectorAll('tbody tr');
      expect(cells(tx[0])).toEqual(['2026-06-05', 'MSFT', '買進', '2', '321.50', '新增']);
      expect(tx[0].classList.contains('dimmed')).toBe(false);
      expect(tx[1].classList.contains('dimmed')).toBe(true);
      expect(norm(tx[1].textContent)).toContain('重複 · 略過');
      expect(cells(tables[1].querySelector('tbody tr'))).toEqual(['2026-06-05', 'deposit', '2500.00', 'USD']);
      expect(Array.from(preview.querySelectorAll('.count-tags app-tag')).map(t => norm(t.textContent))).toEqual(['解析 3', '重複 1', '錯誤 0']);
      expect(norm(preview.textContent)).not.toMatch(/可新增|已新增/);
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 2 筆');
      expect(text(fixture)).not.toContain('確認股票代號');
    });

    it('需要修正的列 lists the 200-body errors and keeps the file', () => {
      const fixture = withBrokerFile({ ...brokerNested, errors: [{ row_index: 3, reason: 'missing FX rate for 2026-06-02 USD' }] });
      const fix = card(fixture, 'fix-card')!;
      expect(norm(fix.querySelector('h2')?.textContent)).toBe('需要修正的列');
      expect(Array.from(fix.querySelectorAll('.fix-line')).map(l => norm(l.textContent)))
        .toEqual(['第 3 列 · missing FX rate for 2026-06-02 USD · 此列會被略過']);
      expect(fixture.componentInstance.file()).not.toBeNull();
    });

    it('需要修正的列 lists the errors of an HTTP error body and keeps the file', () => {
      service.uploadBrokerCsv.mockReturnValue(throwError(() => ({ status: 422, error: { errors: [{ row_index: 3, reason: 'missing FX rate for 2026-06-02 USD' }] } })));
      const fixture = setup({ type: 'broker' });
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'cs.csv')] });
      fixture.detectChanges();
      expect(Array.from(card(fixture, 'fix-card')!.querySelectorAll('.fix-line')).map(l => norm(l.textContent)))
        .toEqual(['第 3 列 · missing FX rate for 2026-06-02 USD · 此列會被略過']);
      expect(fixture.componentInstance.file()).not.toBeNull();
    });

    it('a string detail in the HTTP error body is shown under 需要修正的列 and keeps the file', () => {
      service.uploadCsv.mockReturnValue(throwError(() => ({ status: 400, error: { detail: 'CSV 編碼不是 UTF-8' } })));
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(norm(card(fixture, 'fix-card')?.textContent)).toContain('CSV 編碼不是 UTF-8');
      expect(fixture.componentInstance.file()).not.toBeNull();
    });

    it('a shared {code, message, trace_id} body from 預覽 is shown under 需要修正的列 without a row prefix; file and chip stay', () => {
      service.uploadCsv.mockReturnValue(throwError(() => ({ status: 400, error: { code: 400, message: 'missing column trade_date', trace_id: null } })));
      const fixture = setup();
      const file = new File(['x'], 'a.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      expect(Array.from(card(fixture, 'fix-card')!.querySelectorAll('.fix-line')).map(l => norm(l.textContent)))
        .toEqual(['missing column trade_date']);
      expect(fixture.componentInstance.file()).toBe(file);
      expect(el(fixture).querySelector('app-file-chip')).not.toBeNull();
    });

    it('a pydantic 422 detail array is one reason line, never 第 0 列 rows', () => {
      service.uploadCsv.mockReturnValue(throwError(() => ({ status: 422, error: { detail: [{ loc: ['body', 'file'], msg: 'Field required', type: 'missing' }] } })));
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(fixture.componentInstance.uploadErrors()).toEqual([]);
      expect(Array.from(card(fixture, 'fix-card')!.querySelectorAll('.fix-line')).map(l => norm(l.textContent)))
        .toEqual(['Field required']);
    });

    it('a whole-batch Cathay commit error (row_index 0) renders the reason alone', () => {
      const fixture = withFile(genericPreview);
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false, created: 0, errors: [{ row_index: 0, message: 'IntegrityError: duplicate key' }] }));
      commitButton(fixture).click();
      fixture.detectChanges();
      expect(Array.from(card(fixture, 'fix-card')!.querySelectorAll('.fix-line')).map(l => norm(l.textContent)))
        .toEqual(['IntegrityError: duplicate key']);
    });

    it('確認匯入 is disabled right after a commit that keeps the file; 重新預覽 returns to step 2 and re-arms it with the new N', () => {
      const rows = (a: ImportRow['status'], b: ImportRow['status']) => [csvRow(2, { name: 'A', type: 'BUY' }, a), csvRow(3, { name: 'B', type: 'BUY' }, b)];
      const fixture = withFile({ ...preview, rows: rows('new', 'new') });
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 2 筆');
      service.uploadCsv.mockReturnValue(of({
        ...preview, dry_run: false, created: 1, errors: [{ row_index: 3, message: 'bad' }], rows: rows('new', 'error'),
      }));
      commitButton(fixture).click();
      fixture.detectChanges();
      const component = fixture.componentInstance;
      expect(component.file()).not.toBeNull();
      expect(component.committed()).toBe(true);
      expect(activeStep(fixture)).toContain('匯入並重算');
      expect(commitButton(fixture).disabled).toBe(true);
      service.uploadCsv.mockReturnValue(of({ ...preview, rows: rows('duplicate', 'new') }));
      button(fixture, '重新預覽')!.click();
      fixture.detectChanges();
      expect(component.committed()).toBe(false);
      expect(component.step()).toBe(2);
      expect(activeStep(fixture)).toContain('預覽與確認');
      expect(el(fixture).querySelectorAll('ol[aria-label="匯入步驟"] li.done').length).toBe(1);
      expect(commitButton(fixture).disabled).toBe(false);
      expect(norm(commitButton(fixture).textContent)).toBe('確認匯入 1 筆');
    });

    it('the commit toast says 更新 {rehashed} 筆', () => {
      const fixture = withFile(genericPreview);
      const add = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false, created: 1, errors: [], rehashed: 2 }));
      commitButton(fixture).click();
      const detail = String(add.mock.calls.find(([m]) => m.summary === '匯入完成')?.[0].detail);
      expect(detail).toContain('更新 2 筆');
      expect(detail).not.toContain('重算指紋');
    });

    it('the commit toast lists 已由對帳單匯入 {covered} 筆 when > 0', () => {
      const fixture = withFile(genericPreview);
      const add = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false, created: 1, errors: [], covered: 4 }));
      commitButton(fixture).click();
      const detail = String(add.mock.calls.find(([m]) => m.summary === '匯入完成')?.[0].detail);
      expect(detail).toContain('已由對帳單匯入 4 筆');
    });

    it('確認股票代號 renders one block per name with meta, 驗證代號 and the confirmation checkbox', async () => {
      service.verifyOverrideSymbol.mockReturnValue(of({ status: 'name_mismatch', expected_name: '台灣50正2', fetched_name: '元大台灣50正2' }));
      const fixture = withFile(genericPreview);
      const component = fixture.componentInstance;
      const block = card(fixture, 'override-card')!.querySelector('.override-block')!;
      expect(norm(block.querySelector('.ov-name b')?.textContent)).toBe('台灣50正2 的股票代號');
      expect(norm(block.querySelector('.ov-name small')?.textContent)).toBe('3 筆 · 10/21、10/14、09/30');
      const input = block.querySelector<HTMLInputElement>('input[aria-label="台灣50正2 的股票代號"]')!;
      input.value = '00631L';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
      expect(component.nameOverrides()).toEqual({ 台灣50正2: '00631L' });
      Array.from(block.querySelectorAll<HTMLButtonElement>('button')).find(b => norm(b.textContent) === '驗證代號')!.click();
      fixture.detectChanges();
      expect(service.verifyOverrideSymbol).toHaveBeenCalledWith('台灣50正2', '00631L', '09/30');
      expect(norm(block.textContent)).toContain('名稱不符：元大台灣50正2');
      await fixture.whenStable();
      const confirm = Array.from(block.querySelectorAll('label')).find(l => l.textContent?.includes('確認使用此代號'))!.querySelector('input')!;
      confirm.click();
      fixture.detectChanges();
      expect(component.confirmedOverrides().has('台灣50正2')).toBe(true);
    });

    it('確認匯入 stays enabled with pending overrides and commits straight away when none is unverified', () => {
      const fixture = withFile(genericPreview);
      expect(fixture.componentInstance.pendingOverrideCount()).toBe(1);
      const commit = commitButton(fixture);
      expect(commit.disabled).toBe(false);
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false }));
      commit.click();
      expect(service.uploadCsv).toHaveBeenLastCalledWith('transactions', expect.any(File), false, true, {}, []);
    });

    it('an unverified override opens the 尚有代號未驗證 dialog and accepting submits the commit', () => {
      const fixture = withFile(genericPreview);
      const confirmation = fixture.debugElement.injector.get(ConfirmationService);
      const confirm = vi.spyOn(confirmation, 'confirm').mockImplementation((c: Confirmation) => { c.accept?.(); return confirmation; });
      fixture.componentInstance.setOverride('台灣50正2', '00631L');
      fixture.detectChanges();
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false }));
      commitButton(fixture).click();
      expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ header: '尚有代號未驗證' }));
      expect(service.uploadCsv).toHaveBeenLastCalledWith('transactions', expect.any(File), false, true, { 台灣50正2: '00631L' }, []);
    });

    it('確認匯入 is disabled without a result and while busy', () => {
      const pending$ = new Subject<ImportResult>();
      service.uploadCsv.mockReturnValue(pending$);
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(commitButton(fixture).disabled).toBe(true);
      pending$.next(genericPreview); pending$.complete();
      fixture.detectChanges();
      expect(commitButton(fixture).disabled).toBe(false);
    });

    it('取消 clears the chip, detected format, preview, errors and symbol blocks', () => {
      const fixture = withFile(genericPreview);
      expect(card(fixture, 'preview-card')).not.toBeNull();
      expect(card(fixture, 'fix-card')).not.toBeNull();
      expect(card(fixture, 'override-card')).not.toBeNull();
      button(fixture, '取消')!.click();
      fixture.detectChanges();
      expect(el(fixture).querySelector('app-file-chip')).toBeNull();
      expect(text(fixture)).not.toContain('偵測格式');
      expect(card(fixture, 'preview-card')).toBeNull();
      expect(card(fixture, 'fix-card')).toBeNull();
      expect(card(fixture, 'override-card')).toBeNull();
      expect(el(fixture).querySelector('.imp-footer')).toBeNull();
      expect(fixture.componentInstance.step()).toBe(1);
    });

    it('a successful 券商 commit shows created / skipped / rejected, clears the file and keeps the result', () => {
      const fixture = withBrokerFile(brokerNested);
      service.uploadBrokerCsv.mockReturnValue(of({ ...brokerNested, detected_broker: 'IB', dry_run: false, counts: { created: 2, skipped: 1, rejected: 0 } }));
      commitButton(fixture).click();
      fixture.detectChanges();
      expect(service.uploadBrokerCsv).toHaveBeenLastCalledWith(expect.any(File), false);
      expect(norm(card(fixture, 'preview-card')?.querySelector('.commit-counts')?.textContent)).toBe('已新增 2；重複 1；錯誤 0');
      expect(fixture.componentInstance.file()).toBeNull();
      expect(fixture.componentInstance.brokerResult()).not.toBeNull();
      expect(el(fixture).querySelector('app-file-chip')).toBeNull();
      expect(fixture.componentInstance.step()).toBe(3);
      expect(fixture.componentInstance.committed()).toBe(true);
      expect(activeStep(fixture)).toContain('匯入並重算');
      expect(recalcCard(fixture)).not.toBeNull();
    });

    it('券商: a parser row error lists row index and reason under 需要修正的列 and keeps the file (broker spec)', () => {
      service.uploadBrokerCsv.mockReturnValue(throwError(() => ({ status: 422, error: { errors: [{ row_index: 3, reason: 'missing FX rate for 2026-06-02 USD' }] } })));
      const fixture = setup({ type: 'broker' });
      const file = new File(['x'], 'ft.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      const fix = card(fixture, 'fix-card')!;
      expect(norm(fix.querySelector('h2')?.textContent)).toBe('需要修正的列');
      expect(Array.from(fix.querySelectorAll('.fix-line')).map(l => norm(l.textContent)))
        .toEqual(['第 3 列 · missing FX rate for 2026-06-02 USD · 此列會被略過']);
      expect(fixture.componentInstance.file()).toBe(file);
      expect(el(fixture).querySelector('app-file-chip')).not.toBeNull();
    });

    it('a 券商 commit with row errors keeps the file', () => {
      const fixture = withBrokerFile(brokerNested);
      service.uploadBrokerCsv.mockReturnValue(of({ ...brokerNested, dry_run: false, counts: { created: 1, skipped: 1, rejected: 1 }, errors: [{ row_index: 3, reason: 'bad' }] }));
      commitButton(fixture).click();
      fixture.detectChanges();
      expect(fixture.componentInstance.file()).not.toBeNull();
      expect(norm(card(fixture, 'preview-card')?.querySelector('.commit-counts')?.textContent)).toBe('已新增 1；重複 1；錯誤 1');
      expect(Array.from(card(fixture, 'fix-card')!.querySelectorAll('.fix-line')).map(l => norm(l.textContent))).toEqual(['第 3 列 · bad · 此列會被略過']);
    });

    it('a commit response with row statuses keeps 重複 · 略過 and .dimmed in the kept grid', () => {
      const fixture = withFile({ ...preview, rows: [csvRow(2, { name: 'A', type: 'BUY' }, 'new'), csvRow(3, { name: 'B', type: 'BUY' }, 'duplicate')] });
      service.uploadCsv.mockReturnValue(of({
        ...preview, dry_run: false, created: 1, skipped_duplicates: 1,
        rows: [csvRow(2, { name: 'A', type: 'BUY' }, 'new'), csvRow(3, { name: 'B', type: 'BUY' }, 'duplicate')],
      }));
      commitButton(fixture).click();
      fixture.detectChanges();
      const rows = Array.from(card(fixture, 'preview-card')!.querySelectorAll('tbody tr'));
      expect(rows.map(r => norm(r.querySelector('td:last-child')?.textContent))).toEqual(['新增', '重複 · 略過']);
      expect(rows.map(r => r.classList.contains('dimmed'))).toEqual([false, true]);
      expect(fixture.componentInstance.committed()).toBe(true);
    });

    it('a clean 交易 commit clears the file and hides 確認股票代號', () => {
      const fixture = withFile({ ...genericPreview, errors: [] });
      fixture.componentInstance.setOverride('台灣50正2', '00631L');
      fixture.componentInstance.localValidations.set(new Map([['台灣50正2', { status: 'verified' as const }]]));
      fixture.detectChanges();
      expect(card(fixture, 'override-card')).not.toBeNull();
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false, created: 38, errors: [], unresolved_names: [] }));
      commitButton(fixture).click();
      fixture.detectChanges();
      expect(fixture.componentInstance.file()).toBeNull();
      expect(card(fixture, 'override-card')).toBeNull();
      expect(card(fixture, 'preview-card')).not.toBeNull();
    });

    it('keeps the footer with 取消 after a clean commit', () => {
      const fixture = withFile({ ...genericPreview, errors: [] });
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false, created: 38, errors: [], unresolved_names: [] }));
      commitButton(fixture).click();
      fixture.detectChanges();
      expect(fixture.componentInstance.file()).toBeNull();
      expect(el(fixture).querySelector('.imp-footer')).not.toBeNull();
      expect(button(fixture, '取消')!.disabled).toBe(false);
      expect(commitButton(fixture).disabled).toBe(true);
      expect(button(fixture, '重新預覽')!.disabled).toBe(true);
      button(fixture, '取消')!.click();
      fixture.detectChanges();
      expect(el(fixture).querySelector('.imp-footer')).toBeNull();
      expect(fixture.componentInstance.result()).toBeNull();
    });

    it('a CSV commit with skipped_unresolved keeps the file and the 確認股票代號 card', () => {
      const fixture = withFile({ ...genericPreview, errors: [] });
      service.uploadCsv.mockReturnValue(of({ ...genericPreview, dry_run: false, created: 37, errors: [], skipped_unresolved: 3 }));
      commitButton(fixture).click();
      fixture.detectChanges();
      expect(fixture.componentInstance.file()).not.toBeNull();
      expect(card(fixture, 'override-card')).not.toBeNull();
    });

    it('empty page: 重新預覽 is disabled without a file and 確認匯入 without a result', () => {
      const fixture = setup();
      expect(fixture.componentInstance.file()).toBeNull();
      service.uploadCsv.mockReturnValue(throwError(() => ({ status: 400, error: { detail: 'CSV 編碼不是 UTF-8' } })));
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      fixture.detectChanges();
      expect(fixture.componentInstance.file()).not.toBeNull();
      expect(fixture.componentInstance.activeResult()).toBeNull();
      expect(button(fixture, '重新預覽')!.disabled).toBe(false);
      expect(commitButton(fixture).disabled).toBe(true);
    });

    it('確認匯入 is disabled while a re-preview is in flight with a result shown', () => {
      const fixture = withFile(genericPreview);
      expect(commitButton(fixture).disabled).toBe(false);
      const again$ = new Subject<ImportResult>();
      service.uploadCsv.mockReturnValue(again$);
      button(fixture, '重新預覽')!.click();
      fixture.detectChanges();
      expect(fixture.componentInstance.activeResult()).not.toBeNull();
      expect(commitButton(fixture).disabled).toBe(true);
      again$.next(genericPreview); again$.complete();
      fixture.detectChanges();
      expect(commitButton(fixture).disabled).toBe(false);
    });
  });
  describe('banner, recent imports, side column', () => {
    const norm = (t: string | null | undefined) => (t ?? '').replace(/\s+/g, ' ').trim();
    const banner = (fixture: { nativeElement: HTMLElement }) => norm(el(fixture).querySelector('.import-banner')?.textContent);
    const history = () => TestBed.inject(ImportHistoryService);
    const NOW = new Date(2026, 9, 21, 22, 10);

    function commitWith(response: Observable<ImportResult>, name = 'a.csv') {
      vi.useFakeTimers({ toFake: ['Date'] });
      vi.setSystemTime(NOW);
      const fixture = setup();
      const component = fixture.componentInstance;
      component.onSelect({ files: [new File(['x'], name)] });
      service.uploadCsv.mockReturnValue(response);
      component.commit();
      fixture.detectChanges();
      return fixture;
    }

    it('a commit HTTP 500 records a failed attempt with the reason and shows banner (a)', () => {
      const fixture = commitWith(throwError(() => ({ status: 500, error: { detail: '伺服器錯誤' } })));
      expect(history().attempts()[0]).toMatchObject({ ok: false, file: 'a.csv', type: 'transactions', reason: '伺服器錯誤' });
      expect(banner(fixture)).toBe('最近一次匯入失敗（10/21 22:10）：a.csv，伺服器錯誤，整批未寫入。修正後重新上傳即可。');
    });

    it('a 200 commit with created 0 and one row error shows banner (a) with 有 1 列錯誤', () => {
      const fixture = commitWith(of({ ...preview, dry_run: false, created: 0, errors: [{ row_index: 4, message: '價格欄位為空' }] }));
      expect(history().attempts()[0]).toMatchObject({ ok: false, created: 0, errors: 1 });
      expect(banner(fixture)).toBe('最近一次匯入失敗（10/21 22:10）：a.csv 有 1 列錯誤，整批未寫入。修正後重新上傳即可。');
    });

    it('a partial commit (created 18 with 2 row errors) is a success: no banner, head ok', () => {
      const fixture = commitWith(of({
        ...preview, dry_run: false, created: 18,
        errors: [{ row_index: 4, message: 'x' }, { row_index: 5, message: 'y' }],
      }));
      expect(history().attempts()[0]).toMatchObject({ ok: true, created: 18, errors: 2 });
      expect(el(fixture).querySelector('.import-banner')).toBeNull();
      expect(text(fixture)).not.toContain('整批未寫入');
    });

    it('the next successful commit clears banner (a)', () => {
      const fixture = commitWith(throwError(() => ({ status: 500, error: { detail: 'boom' } })));
      expect(banner(fixture)).toContain('最近一次匯入失敗');
      service.uploadCsv.mockReturnValue(of({ ...preview, dry_run: false, created: 3 }));
      fixture.componentInstance.commit();
      fixture.detectChanges();
      expect(history().attempts().map(a => a.ok)).toEqual([true, false]);
      expect(el(fixture).querySelector('.import-banner')).toBeNull();
    });

    it('created 0 with no errors (all duplicates) records a success without a banner', () => {
      const fixture = commitWith(of({ ...preview, dry_run: false, created: 0, skipped_duplicates: 5, errors: [] }));
      expect(history().attempts()[0]).toMatchObject({ ok: true, created: 0 });
      expect(el(fixture).querySelector('.import-banner')).toBeNull();
    });

    it('a dry-run is not recorded', () => {
      const fixture = setup();
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
      expect(history().attempts()).toEqual([]);
    });

    it('a 券商 commit failure is recorded with the broker type', () => {
      const fixture = setup({ type: 'broker' });
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'ft.csv')] });
      service.uploadBrokerCsv.mockReturnValue(throwError(() => ({ status: 502 })));
      fixture.componentInstance.commit();
      expect(history().attempts()[0]).toMatchObject({ ok: false, file: 'ft.csv', type: 'broker', reason: 'HTTP 502' });
    });

    it('a 券商 200 commit records {ok: true, type: broker, created: counts.created} as the head attempt', () => {
      const fixture = setup({ type: 'broker' });
      fixture.componentInstance.onSelect({ files: [new File(['x'], 'ft.csv')] });
      service.uploadBrokerCsv.mockReturnValue(of({ ...brokerPreview, dry_run: false, counts: { created: 6, skipped: 1, rejected: 0 } }));
      fixture.componentInstance.commit();
      fixture.detectChanges();
      expect(history().attempts()[0]).toMatchObject({ ok: true, file: 'ft.csv', type: 'broker', created: 6, errors: 0 });
      expect(el(fixture).querySelector('.import-banner')).toBeNull();
    });

    const FAILED_HEAD = { at: NOW.toISOString(), file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1, reason: null };

    it('a failed head attempt seeded in localStorage shows banner (a) on first load', () => {
      localStorage.setItem('hh-import-history', JSON.stringify([FAILED_HEAD]));
      const fixture = setup();
      expect(history().attempts()[0]).toMatchObject({ ok: false, file: 'a.csv' });
      expect(el(fixture).querySelectorAll('.import-banner').length).toBe(1);
      expect(banner(fixture)).toBe('最近一次匯入失敗（10/21 22:10）：a.csv 有 1 列錯誤，整批未寫入。修正後重新上傳即可。');
    });

    it('a failed head attempt and a failed recalc show two banners, (a) first', () => {
      localStorage.setItem('hh-import-history', JSON.stringify([FAILED_HEAD]));
      service.getRecalcStatus.mockReturnValue(of({
        kind: 'import', state: 'failed', steps: [{ name: 'dividend_auto_record', status: 'failed', error: 'TWSE timeout' }],
      }));
      const banners = Array.from(el(setup()).querySelectorAll('.import-banner')).map(b => norm(b.textContent));
      expect(banners).toEqual([
        '最近一次匯入失敗（10/21 22:10）：a.csv 有 1 列錯誤，整批未寫入。修正後重新上傳即可。',
        '最近一次匯入的重算失敗：TWSE timeout',
      ]);
    });

    it('a failed recalc alone shows banner (b) with the reason and no nothing-written sentence', () => {
      service.getRecalcStatus.mockReturnValue(of({
        kind: 'import', state: 'failed', current_step: 'dividend_auto_record',
        steps: [{ name: 'dividend_auto_record', status: 'failed', error: 'TWSE timeout' }],
      }));
      const fixture = setup();
      const banners = el(fixture).querySelectorAll('.import-banner');
      expect(banners.length).toBe(1);
      expect(norm(banners[0].textContent)).toBe('最近一次匯入的重算失敗：TWSE timeout');
      expect(text(fixture)).not.toContain('整批未寫入');
      expect(text(fixture)).not.toContain('沒有寫入任何資料');
      expect(text(fixture)).toContain('匯入重算：失敗');
    });

    it('a failed recalc without a step error names the current step label', () => {
      service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'failed', current_step: 'networth_backfill', steps: [] }));
      const banners = Array.from(el(setup()).querySelectorAll('.import-banner')).map(b => norm(b.textContent));
      expect(banners).toEqual(['最近一次匯入的重算失敗：淨值回補']);
    });

    it('a failed quote-only refresh shows no banner', () => {
      service.getRecalcStatus.mockReturnValue(of({ kind: 'quotes', state: 'failed' }));
      expect(el(setup()).querySelector('.import-banner')).toBeNull();
    });

    it('the recalc panel is hidden on load with an idle status and no file, shown while running', () => {
      expect(text(setup())).not.toContain('匯入重算：');
      TestBed.resetTestingModule();
      vi.useFakeTimers();
      service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'running' }));
      const fixture = setup();
      expect(text(fixture)).toContain('匯入重算：執行中');
      fixture.destroy();
    });

    describe('side column', () => {
      const aside = (fixture: { nativeElement: HTMLElement }) =>
        el(fixture).querySelector<HTMLElement>('aside[aria-label="匯入後會發生什麼"]');
      const at = (d: number, h: number, mi: number) => new Date(2026, 9, d, h, mi).toISOString();
      const pair = (r: Element) => [norm(r.querySelector('b')?.textContent), norm(r.querySelector('small')?.textContent)];

      function inMode(mode: 'panes' | 'sheet' | 'phone') {
        const fixture = setup();
        TestBed.inject(LayoutModeService).set(mode);
        fixture.detectChanges();
        return fixture;
      }
      function inModeWithFile(mode: 'panes' | 'sheet' | 'phone') {
        const fixture = inMode(mode);
        fixture.componentInstance.onSelect({ files: [new File(['x'], 'a.csv')] });
        fixture.detectChanges();
        return fixture;
      }

      it('panes: 匯入後自動執行 with the three rows verbatim beside the main column', () => {
        const fixture = inMode('panes');
        const side = aside(fixture)!;
        expect(side.parentElement?.classList).toContain('imp-layout');
        const after = side.querySelector('app-bento.after-card')!;
        expect(norm(after.querySelector('h2')?.textContent)).toBe('匯入後自動執行');
        expect(Array.from(after.querySelectorAll('.after-row')).map(pair)).toEqual([
          ['重算持股與損益', '依新交易重建成本、已實現損益與當沖標記'],
          ['抓取除權息事件', '新股票的除權息會記為待收股利，需到股利頁確認收款'],
          ['更新報價', '回補收盤價，不含股利'],
        ]);
      });

      it('panes: 最近匯入 lists the attempts newest first with ok / failed dots', () => {
        const fixture = setup();
        history().record({ at: at(12, 20, 5), file: '國泰證券_股利_2026Q3.csv', type: 'dividends', ok: true, created: 4 });
        history().record({ at: at(1, 21, 42), file: '元大_對帳單_202609.csv', type: 'broker', ok: true, created: 18 });
        history().record({ at: at(21, 22, 10), file: '元大證券_成交明細_202610.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
        TestBed.inject(LayoutModeService).set('panes');
        fixture.detectChanges();
        const recent = aside(fixture)!.querySelector('app-bento.recent-card')!;
        expect(norm(recent.querySelector('h2')?.textContent)).toBe('最近匯入');
        const rows = Array.from(recent.querySelectorAll('.recent-row'));
        expect(rows.map(pair)).toEqual([
          ['元大證券_成交明細_202610.csv', '10/21 22:10 · 失敗 · 1 列錯誤'],
          ['元大_對帳單_202609.csv', '10/01 21:42 · 成功 · 新增 18 筆'],
          ['國泰證券_股利_2026Q3.csv', '10/12 20:05 · 成功 · 新增 4 筆'],
        ]);
        expect(rows.map(r => r.querySelector('.dot')?.classList.contains('failed'))).toEqual([true, false, false]);
        expect(rows.map(r => r.querySelector('.dot')?.classList.contains('ok'))).toEqual([false, true, true]);
      });

      it('尚無匯入紀錄 when the device has no attempts', () => {
        const recent = aside(inMode('panes'))!.querySelector('app-bento.recent-card')!;
        expect(recent.querySelectorAll('.recent-row').length).toBe(0);
        expect(norm(recent.textContent)).toContain('尚無匯入紀錄');
      });

      for (const mode of ['sheet', 'phone'] as const) {
        it(`${mode}: the two cards render under the footer in the main column`, () => {
          const fixture = inModeWithFile(mode);
          const side = aside(fixture)!;
          const footer = el(fixture).querySelector('.imp-footer')!;
          expect(side.parentElement).toBe(footer.parentElement);
          expect(footer.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
          expect(el(fixture).querySelector('.imp-layout > aside')).toBeNull();
          expect(side.querySelectorAll('app-bento').length).toBe(2);
        });

        it(`${mode}: with the recalc card visible the order is footer → 匯入並重算 → side cards`, () => {
          service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'failed' }));
          const fixture = inModeWithFile(mode);
          const footer = el(fixture).querySelector('.imp-footer')!;
          const card = recalcCard(fixture)!;
          const side = aside(fixture)!;
          expect(card).not.toBeNull();
          expect(footer.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
          expect(card.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
          expect(side.contains(card)).toBe(false);
        });
      }

      it('panes: the recalc card stays in the main column, not in the side column', () => {
        service.getRecalcStatus.mockReturnValue(of({ kind: 'import', state: 'failed' }));
        const fixture = inMode('panes');
        const card = recalcCard(fixture)!;
        expect(card).not.toBeNull();
        expect(card.closest('.imp-main')).not.toBeNull();
        expect(aside(fixture)!.contains(card)).toBe(false);
        expect(aside(fixture)!.parentElement?.classList).toContain('imp-layout');
      });
    });
  });
});
