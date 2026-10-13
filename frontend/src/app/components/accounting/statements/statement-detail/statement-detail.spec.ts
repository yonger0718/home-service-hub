import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountPeriodSummary, LedgerEntry } from '../../../../models/accounting.model';
import { LayoutModeService } from '../../../../services/layout-mode.service';
import { AccountingToastService } from '../../accounting-toast';
import { formatMoney } from '../../format';
import { makeEntry } from '../../testing/fixtures';
import { ReconciliationCase, StatementDetail, StatementLine } from '../statement.model';
import { StatementDetailComponent } from './statement-detail';

function statement(overrides: Partial<StatementDetail> = {}): StatementDetail {
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
    statement_total: '100.0000',
    minimum_payment: null,
    origin: 'import',
    mode: 'live',
    status: 'open',
    conflict_open: false,
    needs_recheck: false,
    current_revision_id: 1,
    matched_count: 1,
    explained_count: 0,
    open_case_count: 2,
    incoming_deferrals_pending: 0,
    lines: [],
    cases: [],
    stale_events_pending: false,
    revisions: [],
    ...overrides,
  };
}

function line(overrides: Partial<StatementLine> = {}): StatementLine {
  return {
    id: 1,
    event_id: 10,
    seq: 1,
    txn_date: null,
    posted_date: '2026-09-01',
    merchant_raw: '誠品書店',
    merchant_norm: '誠品書店',
    printed_amount: '-100.0000',
    flow_amount: '-100.0000',
    foreign_amount: null,
    foreign_currency: null,
    line_kind: 'purchase',
    installment_seq: null,
    installment_total: null,
    matched: true,
    coverage: [
      {
        entry_id: 40,
        group_id: null,
        role: 'principal',
        flow: '-100.0000',
        match_rule: 'exact',
        match_kind: 'auto',
        status: 'active',
      },
    ],
    ...overrides,
  };
}

function item(overrides: Partial<ReconciliationCase> = {}): ReconciliationCase {
  return {
    id: 4,
    kind: 'line_unmatched',
    status: 'open',
    event_id: 11,
    line_id: 2,
    entry_id: null,
    explanation: null,
    candidates: [],
    context: {},
    version: 1,
    created_at: '2026-09-21T00:00:00Z',
    ...overrides,
  };
}

function summary(overrides: Partial<AccountPeriodSummary> = {}): AccountPeriodSummary {
  return {
    account_id: 6,
    currency: 'TWD',
    date_from: '2026-08-21',
    date_to: '2026-09-20',
    spend: '-80.0000',
    income: '0',
    rewards: '0',
    net: '-80.0000',
    end_balance: '0',
    count: 1,
    ...overrides,
  };
}

const LUNCH: LedgerEntry = makeEntry({
  id: 40,
  name: '午餐',
  amount: '-80.0000',
  posted_date: '2026-09-02',
});

describe('StatementDetailComponent', () => {
  let http: HttpTestingController;
  let params: BehaviorSubject<ParamMap>;

  beforeEach(() => {
    params = new BehaviorSubject(convertToParamMap({ id: '6', sid: '1' }));
    TestBed.configureTestingModule({
      imports: [StatementDetailComponent],
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

  function render(): ComponentFixture<StatementDetailComponent> {
    const fixture = TestBed.createComponent(StatementDetailComponent);
    fixture.detectChanges();
    return fixture;
  }

  function flushDetail(
    body: StatementDetail,
    ledger: AccountPeriodSummary | 'error' = summary(),
    entries: LedgerEntry[] = [],
  ): void {
    http.expectOne('/api/accounting/accounts/6/statements/1').flush(body);
    if (ledger === 'error') {
      http
        .expectOne((r) => r.url === '/api/accounting/accounts/6/summary')
        .flush('nope', { status: 500, statusText: 'Server Error' });
    } else {
      const req = http.expectOne((r) => r.url === '/api/accounting/accounts/6/summary');
      expect(req.request.params.get('date_from')).toBe(body.period_start);
      expect(req.request.params.get('date_to')).toBe(body.period_end);
      req.flush(ledger);
    }
    const entriesReq = http.expectOne((r) => r.url === '/api/accounting/accounts/6/entries');
    expect(entriesReq.request.params.get('limit')).toBe('500');
    entriesReq.flush({ items: entries, total: entries.length, limit: 500, offset: 0 });
  }

  function loaded(
    body: StatementDetail,
    ledger?: AccountPeriodSummary | 'error',
    entries: LedgerEntry[] = [],
  ): ComponentFixture<StatementDetailComponent> {
    const fixture = render();
    flushDetail(body, ledger, entries);
    fixture.detectChanges();
    return fixture;
  }

  it('compares a card bill with the period spend and warns on a visible delta', () => {
    const fixture = loaded(statement({ needs_recheck: true, stale_events_pending: true }));
    const el = fixture.nativeElement as HTMLElement;
    expect(text(el.querySelector('.period'))).toContain('2026/08/21');
    expect(text(el.querySelector('.period'))).toContain('2026/09/20');
    expect(Array.from(el.querySelectorAll('.mode')).map(text)).toEqual([
      '對帳中',
      '需重新檢查',
      '需重新同步',
    ]);
    const labels = Array.from(el.querySelectorAll('.compare small')).map(text);
    expect(labels).toEqual(['帳單應繳', '帳本本期', '差額']);
    expect(text(el.querySelector('.delta'))).toBe(formatMoney(20, 'TWD', { sign: true }));
    expect(el.querySelector('.delta')!.classList).toContain('warn');
    expect(el.querySelector('.confirm')!.hasAttribute('disabled')).toBe(true);
    expect(text(el.querySelector('.reason'))).toBe('全部確認暫不可用：仍有未處理項目');
  });

  it('compares a bank ending balance with the ledger balance', () => {
    const fixture = loaded(
      statement({ kind: 'bank', statement_total: '227189.0000', open_case_count: 0 }),
      summary({ spend: '-100.0000', end_balance: '227204.0000' }),
    );
    const el = fixture.nativeElement as HTMLElement;
    expect(Array.from(el.querySelectorAll('.compare small')).map(text)).toEqual([
      '期末餘額',
      '帳本餘額',
      '差額',
    ]);
    expect(el.querySelector('.delta')!.classList).toContain('warn');
    expect(el.querySelector('.confirm')!.hasAttribute('disabled')).toBe(false);
    expect(el.querySelector('.reason')).toBeNull();
  });

  it('keeps the statement when the ledger summary fails', () => {
    const fixture = loaded(statement(), 'error');
    const el = fixture.nativeElement as HTMLElement;
    expect(text(el.querySelector('.period'))).toContain('2026/09/20');
    expect(text(el.querySelector('.compare .load-error'))).toBe('帳本讀取失敗');
    expect(el.querySelector('.load-error-block')).toBeNull();
  });

  it('hides actions on a historical statement', () => {
    const fixture = loaded(statement({ mode: 'historical', status: 'reconciled' }));
    const el = fixture.nativeElement as HTMLElement;
    expect(text(el.querySelector('.mode'))).toBe('歷史');
    expect(el.querySelector('.actions')).toBeNull();
  });

  it('splits the three segments and shows a matched row read-only', () => {
    const body = statement({
      lines: [
        line({ event_flag: 'text_changed', event_status: 'live' }),
        line({
          id: 2,
          event_id: 11,
          merchant_raw: '屈臣氏',
          merchant_norm: '屈臣氏',
          printed_amount: '-50.0000',
          flow_amount: '-50.0000',
          matched: false,
          coverage: [],
        }),
      ],
      cases: [
        item(),
        item({ id: 8, kind: 'entry_unmatched', event_id: null, line_id: null, entry_id: 40 }),
        item({
          id: 9,
          kind: 'balance_gap',
          event_id: null,
          line_id: null,
          context: { gap: '-15.0000' },
        }),
        item({
          id: 3,
          kind: 'parse_review',
          context: { quarantined_event_id: 11, equivalence: 'changed' },
        }),
      ],
    });
    const fixture = loaded(body, summary(), [LUNCH]);
    const el = fixture.nativeElement as HTMLElement;
    expect(text(el.querySelector('[role="radio"][aria-checked="true"]'))).toContain('已對帳 1');

    const matched = el.querySelector('.row')!;
    expect(text(matched.querySelector('.name'))).toBe('誠品書店');
    expect(Array.from(matched.querySelectorAll('.pill')).map(text)).toContain('文字已變更');
    matched.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
    const panel = text(el.querySelector('.match-panel'));
    expect(panel).toContain('對帳規則：金額與日期相符');
    expect(panel).toContain('本金 午餐');
    expect(el.querySelector('.match-panel button')).toBeNull();

    el.querySelectorAll<HTMLButtonElement>('[role="radio"]')[1].click();
    fixture.detectChanges();
    const statementRow = el.querySelector('.row')!;
    expect(text(statementRow.querySelector('.name'))).toBe('屈臣氏');
    expect(Array.from(statementRow.querySelectorAll('.pill')).map(text)).toEqual(
      expect.arrayContaining(['資料已變更，待確認', '僅帳單有']),
    );

    el.querySelectorAll<HTMLButtonElement>('[role="radio"]')[2].click();
    fixture.detectChanges();
    const ledgerRows = Array.from(el.querySelectorAll('.row'));
    expect(ledgerRows.map((row) => text(row.querySelector('.name')))).toEqual(['午餐', '餘額差異']);
    ledgerRows[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
    expect(text(el.querySelector('.match-panel'))).toContain('待處理，操作在下一步。');
    expect(el.querySelector('.match-panel button')).toBeNull();
  });

  it('ignores a stale detail when the statement id changes', () => {
    const fixture = render();
    const first = http.expectOne('/api/accounting/accounts/6/statements/1');
    params.next(convertToParamMap({ id: '6', sid: '2' }));
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-skeleton')).not.toBeNull();

    first.flush(statement({ period_end: '2026-09-20' }));
    fixture.detectChanges();
    expect(el.querySelector('.period')).toBeNull();

    http
      .expectOne('/api/accounting/accounts/6/statements/2')
      .flush(statement({ id: 2, period_start: '2026-09-01', period_end: '2026-09-30' }));
    http
      .expectOne((r) => r.url === '/api/accounting/accounts/6/summary')
      .flush(summary({ date_from: '2026-09-01', date_to: '2026-09-30' }));
    http
      .expectOne((r) => r.url === '/api/accounting/accounts/6/entries')
      .flush({ items: [], total: 0, limit: 500, offset: 0 });
    fixture.detectChanges();
    expect(text(el.querySelector('.period'))).toContain('2026/09/30');
    expect(text(el.querySelector('.period'))).not.toContain('2026/08/21');
  });

  it('asks for a note before 強制確認 and lists the checks a refusal returns', () => {
    const fixture = loaded(statement());
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('.force')!.click();
    fixture.detectChanges();

    const note = el.querySelector<HTMLTextAreaElement>('.force-note')!;
    note.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
    );
    fixture.detectChanges();
    http.expectNone((r) => r.method === 'POST');
    expect(el.querySelector('.sheet')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('.force-submit')!.click();
    fixture.detectChanges();
    expect(text(el.querySelector('.sheet .load-error'))).toBe('強制確認需要填寫備註');
    http.expectNone((r) => r.method === 'POST');

    note.value = '帳差先收下';
    note.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
    note.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
    );
    fixture.detectChanges();
    http.expectNone((r) => r.method === 'POST');

    note.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    fixture.detectChanges();
    expect(text(el.querySelector('.discard'))).toContain('放棄未儲存的備註');
    Array.from(el.querySelectorAll<HTMLButtonElement>('.sheet button'))
      .find((button) => text(button) === '留下')!
      .click();
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('.force-submit')!.click();
    fixture.detectChanges();
    const post = http.expectOne(
      (r) => r.method === 'POST' && r.url === '/api/accounting/accounts/6/statements/1/confirm',
    );
    expect(post.request.body).toEqual({ force: true, note: '帳差先收下' });
    post.flush(
      { message: `confirm_refused: ${JSON.stringify({ open_cases: false, conserved: true })}` },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    expect(el.querySelector('.sheet')).toBeNull();
    expect(Array.from(el.querySelectorAll('.refusal li')).map(text)).toEqual(['仍有未處理項目']);
  });

  it('keeps a whitespace note on the client and shows the server note error', () => {
    const fixture = loaded(statement());
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('.force')!.click();
    fixture.detectChanges();
    const note = el.querySelector<HTMLTextAreaElement>('.force-note')!;
    note.value = ' ';
    note.dispatchEvent(new Event('input', { bubbles: true }));
    el.querySelector<HTMLButtonElement>('.force-submit')!.click();
    fixture.detectChanges();
    expect(text(el.querySelector('.sheet .load-error'))).toBe('強制確認需要填寫備註');
    http.expectNone((r) => r.method === 'POST');

    note.value = '先記一筆';
    note.dispatchEvent(new Event('input', { bubbles: true }));
    el.querySelector<HTMLButtonElement>('.force-submit')!.click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.url.endsWith('/confirm'))
      .flush(
        { detail: [{ loc: ['body', 'note'], msg: 'note_required', type: 'value_error' }] },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
    fixture.detectChanges();
    expect(text(el.querySelector('.sheet .load-error'))).toBe('強制確認需要填寫備註');
    expect(el.querySelector('.sheet')).not.toBeNull();
  });

  it('toasts when confirm or reconcile is a replay, and when reconcile is skipped', () => {
    const toast = vi.spyOn(TestBed.inject(AccountingToastService), 'show');
    const fixture = loaded(statement({ open_case_count: 0 }));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('.reconcile')!.click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.url.endsWith('/reconcile'))
      .flush({
        claims: 0,
        cases_opened: [],
        explained: 0,
        unmatched_entries: 0,
        skipped: 'parse_review',
        policy: { applied: 0, proposed: 0 },
      });
    fixture.detectChanges();
    expect(toast).toHaveBeenCalledWith('有待確認的變更，這次沒有重跑對帳');
    flushDetail(statement({ open_case_count: 0 }));
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('.force')!.click();
    fixture.detectChanges();
    const note = el.querySelector<HTMLTextAreaElement>('.force-note')!;
    note.value = '再確認一次';
    note.dispatchEvent(new Event('input', { bubbles: true }));
    el.querySelector<HTMLButtonElement>('.force-submit')!.click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.url.endsWith('/confirm'))
      .flush({
        status: 'reconciled',
        checks: {},
        reopened_cases: [],
        forced: true,
        replayed: true,
      });
    fixture.detectChanges();
    expect(toast).toHaveBeenCalledWith('這次與上次相同，沒有重複執行');
    flushDetail(statement({ open_case_count: 0, status: 'reconciled' }));
    fixture.detectChanges();
  });

  it('shows a reconcile conflict in the owner’s words', () => {
    const fixture = loaded(statement());
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('.reconcile')!.click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.url.endsWith('/reconcile'))
      .flush({ detail: 'reconcile_busy' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(text(el.querySelector('.load-error'))).toBe('對帳進行中，請稍後再試');
  });
});
