import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PortfolioService } from '../../../services/portfolio.service';
import { ImportResult } from '../../../models/portfolio.model';
import { PortfolioImportComponent } from './import';

const preview: ImportResult = { parsed: 1, created: 1, skipped_duplicates: 0, dry_run: true, errors: [], created_ids: [], rows: [] };
describe('Portfolio import controls', () => {
  const service = { uploadCsv: vi.fn(), getRecalcStatus: vi.fn(), triggerRecalc: vi.fn() };
  beforeEach(() => {
    vi.clearAllMocks();
    service.uploadCsv.mockReturnValue(of(preview));
    service.getRecalcStatus.mockReturnValue(of({ state: 'idle' }));
    TestBed.configureTestingModule({ imports: [PortfolioImportComponent], providers: [{ provide: PortfolioService, useValue: service }] });
  });
  it('switches the selected file to dividend parsing with fresh overrides and displays dividend columns', () => {
    const fixture = TestBed.createComponent(PortfolioImportComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const file = new File(['symbol,amount,ex_dividend_date\n2330,25,2026-09-04'], 'dividend.csv');
    component.onSelect({ files: [file] });
    component.setOverride('old name', '9999');
    const select: HTMLSelectElement = fixture.nativeElement.querySelector('select');
    select.value = 'dividends'; select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(service.uploadCsv).toHaveBeenLastCalledWith('dividends', file, true, true, {}, []);
    expect(component.nameOverrides()).toEqual({});
    expect(component.kind()).toBe('dividends');
  });
  it('invalidates remote verification and confirmation when an override code changes', () => {
    const component = TestBed.createComponent(PortfolioImportComponent).componentInstance;
    component.result.set({ ...preview, override_validations: [{ name: 'company', code: '2330', status: 'verified' }] });
    component.toggleConfirm('company', true);
    component.setOverride('company', '0050');
    expect(component.validationFor('company')).toBeNull();
    expect(component.confirmedOverrides().has('company')).toBe(false);
  });
  it('prevents duplicate recalc submissions while a request is pending', () => {
    const response = new Subject<unknown>();
    service.triggerRecalc.mockReturnValue(response);
    const component = TestBed.createComponent(PortfolioImportComponent).componentInstance;
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
    const fixture = TestBed.createComponent(PortfolioImportComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('匯入重算：partial');
    expect(text).toContain('報價更新（不含股利）：completed');
    expect(text).toContain('9802');
    expect(text).toContain('2026-09-10');
    expect(text).toContain('3.100000');
    expect(text).toContain('發放日：未知');
    expect(text).toContain('recording deferred');
    expect(text).toContain('detail unavailable');
    expect(text).not.toContain('2026-10-15');
  });
  it('does not label a quote-only run as completed import reconciliation', () => {
    service.getRecalcStatus.mockReturnValue(of({ state: 'completed', kind: 'quotes', quote_refresh: { state: 'completed' } }));
    const fixture = TestBed.createComponent(PortfolioImportComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('匯入重算：idle');
  });

});
