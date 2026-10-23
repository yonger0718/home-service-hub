import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject, of, Subject, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PortfolioService } from '../../../services/portfolio.service';
import { BrokerCsvImportResult, ImportResult } from '../../../models/portfolio.model';
import { PortfolioImportComponent } from './import';

const preview: ImportResult = { parsed: 1, created: 1, skipped_duplicates: 0, dry_run: true, errors: [], created_ids: [], rows: [] };
const brokerPreview: BrokerCsvImportResult = {
  detected_broker: 'firstrade', dry_run: true,
  transactions: [{ row_index: 2, fingerprint: 'a', payload: { symbol: 'AAPL' } }],
  cash_flows: [{ row_index: 3, fingerprint: 'b', payload: { amount: '10' } }],
  counts: { created: 2, skipped: 0, rejected: 0 }, errors: [],
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
  const button = (fixture: { nativeElement: HTMLElement }, label: string) =>
    Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.trim() === label);
  const activeStep = (fixture: { nativeElement: HTMLElement }) =>
    el(fixture).querySelector('ol[aria-label="匯入步驟"] li[aria-current="step"]')?.textContent ?? '';

  beforeEach(() => {
    vi.clearAllMocks();
    service.uploadCsv.mockReturnValue(of(preview));
    service.uploadBrokerCsv.mockReturnValue(of(brokerPreview));
    service.getRecalcStatus.mockReturnValue(of({ state: 'idle' }));
  });
  afterEach(() => {
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
    const content = text(fixture);
    expect(content).toContain('匯入重算：partial');
    expect(content).toContain('報價更新（不含股利）：completed');
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
    expect(text(setup())).toContain('匯入重算：idle');
  });

  it('updates running quote status without losing a partial import', () => {
    vi.useFakeTimers();
    service.getRecalcStatus.mockReturnValue(of({ state: 'partial', kind: 'import', quote_refresh: { state: 'running' } }));
    const fixture = setup();
    service.getRecalcStatus.mockReturnValue(of({ state: 'partial', kind: 'import', quote_refresh: { state: 'completed' } }));
    vi.advanceTimersByTime(5000);
    fixture.detectChanges();
    expect(text(fixture)).toContain('匯入重算：partial');
    expect(text(fixture)).toContain('報價更新（不含股利）：completed');
    fixture.destroy();
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

    it('unchecking 首列為欄位名稱 re-previews with has_header=false', async () => {
      const fixture = setup();
      const file = new File(['x'], 'a.csv');
      fixture.componentInstance.onSelect({ files: [file] });
      fixture.detectChanges();
      await fixture.whenStable(); // ngModel writes the checked state asynchronously
      const checkbox = headerCheckbox(fixture)!.querySelector('input')!;
      checkbox.click();
      fixture.detectChanges();
      expect(service.uploadCsv).toHaveBeenLastCalledWith('transactions', file, true, false, {}, []);
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
});
