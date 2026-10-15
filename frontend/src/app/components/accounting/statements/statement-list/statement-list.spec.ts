import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { LayoutModeService } from '../../../../services/layout-mode.service';
import { StatementSummary } from '../statement.model';
import { StatementListComponent } from './statement-list';

function statement(overrides: Partial<StatementSummary> = {}): StatementSummary {
  return {
    id: 1,
    account_id: 6,
    kind: 'card',
    currency: 'TWD',
    period_start: '2026-08-21',
    period_end: '2026-09-20',
    closing_date: '2026-09-20',
    due_date: '2026-10-05',
    opening_balance: null,
    statement_total: '35861.0000',
    minimum_payment: null,
    origin: 'import',
    mode: 'live',
    status: 'open',
    conflict_open: false,
    needs_recheck: false,
    current_revision_id: 1,
    matched_count: 37,
    explained_count: 0,
    open_case_count: 14,
    incoming_deferrals_pending: 0,
    ...overrides,
  };
}

describe('StatementListComponent', () => {
  let http: HttpTestingController;
  let params: BehaviorSubject<ParamMap>;

  beforeEach(() => {
    params = new BehaviorSubject(convertToParamMap({ id: '6' }));
    TestBed.configureTestingModule({
      imports: [StatementListComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: params } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function render(): ComponentFixture<StatementListComponent> {
    const fixture = TestBed.createComponent(StatementListComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('shows a skeleton, then live rows and the historical summary', () => {
    const fixture = render();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(el.querySelector('.statement-row')).toBeNull();

    http.expectOne('/api/accounting/accounts/6/statements').flush([
      statement(),
      statement({
        id: 8,
        mode: 'historical',
        status: 'reconciled',
        period_start: '2026-07-21',
        period_end: '2026-08-20',
        matched_count: 40,
        explained_count: 2,
        open_case_count: 0,
      }),
      statement({
        id: 7,
        mode: 'historical',
        period_start: '2026-06-21',
        period_end: '2026-07-20',
        matched_count: 10,
        explained_count: 1,
        open_case_count: 3,
        conflict_open: true,
      }),
    ]);
    fixture.detectChanges();

    const live = el.querySelector('.statement-row')!;
    expect(live.getAttribute('href')).toBe('/accounting/accounts/6/statements/1');
    expect(text(live.querySelector('.name'))).toContain('2026/08/21');
    expect(text(live.querySelector('.sub'))).toBe('對帳 37/51');
    expect(text(live.querySelector('.pill'))).toBe('對帳中');

    expect(Array.from(el.querySelectorAll('.history th')).map(text)).toContain('未結案');
    expect(el.querySelector('.history')?.textContent).not.toContain('未對上');
    const rows = Array.from(el.querySelectorAll('.history tbody tr'));
    expect(rows.map((row) => text(row))).toEqual([
      expect.stringContaining('40'),
      expect.stringContaining('10'),
    ]);
    expect(rows[0].querySelector('a')!.getAttribute('href')).toBe(
      '/accounting/accounts/6/statements/8',
    );
    expect(rows[0].querySelectorAll('td')[1].textContent?.trim()).toBe('40');
    expect(rows[0].querySelectorAll('td')[2].textContent?.trim()).toBe('2');
    expect(rows[0].querySelectorAll('td')[3].textContent?.trim()).toBe('0');
    expect(rows[0].querySelectorAll('td')[4].getAttribute('aria-label')).toBe('一致');
    expect(rows[1].querySelectorAll('td')[4].textContent?.trim()).toBe('✗');
    expect(rows[1].querySelectorAll('td')[4].getAttribute('aria-label')).toBe('不一致');
  });

  it('says when there is no live bill and no historical period', () => {
    const fixture = render();
    http.expectOne('/api/accounting/accounts/6/statements').flush([]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(text(el)).toContain('沒有進行中的帳單');
    expect(text(el.querySelector('.empty-history'))).toBe('沒有歷史期間');
  });

  it('shows the read error and retries', () => {
    const fixture = render();
    http
      .expectOne('/api/accounting/accounts/6/statements')
      .flush('nope', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(text(el.querySelector('.load-error'))).toBe('帳單讀取失敗');
    expect(el.querySelector('app-skeleton')).toBeNull();

    el.querySelector<HTMLButtonElement>('.retry')!.click();
    fixture.detectChanges();
    http
      .expectOne('/api/accounting/accounts/6/statements')
      .flush([statement({ matched_count: 1, open_case_count: 0 })]);
    fixture.detectChanges();
    expect(text(el.querySelector('.statement-row .sub'))).toBe('對帳 1/1');
  });

  it('drops a superseded list when the account changes', () => {
    const fixture = render();
    const first = http.expectOne('/api/accounting/accounts/6/statements');
    params.next(convertToParamMap({ id: '3' }));
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-skeleton')).not.toBeNull();

    first.flush([statement({ period_end: '2026-09-20' })]);
    fixture.detectChanges();
    expect(el.querySelector('.statement-row')).toBeNull();

    http
      .expectOne('/api/accounting/accounts/3/statements')
      .flush([
        statement({ id: 2, account_id: 3, period_start: '2026-09-01', period_end: '2026-09-30' }),
      ]);
    fixture.detectChanges();
    expect(text(el.querySelector('.statement-row .name'))).toContain('2026/09/01');
    expect(text(el.querySelector('.statement-row .name'))).not.toContain('2026/08/21');
  });

  it('links back to the passbook on the phone', () => {
    TestBed.inject(LayoutModeService).set('phone');
    const fixture = render();
    http.expectOne('/api/accounting/accounts/6/statements').flush([]);
    fixture.detectChanges();
    const back = (fixture.nativeElement as HTMLElement).querySelector('.back-link')!;
    expect(back.getAttribute('aria-label')).toBe('返回存摺');
    expect(back.getAttribute('href')).toBe('/accounting/accounts/6');
  });
});
