import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountInput, ImportRun, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { AccountingAccountsComponent } from './accounts';

function account(fields: Record<string, unknown>): LedgerAccount {
  return {
    id: 0, name: '', currency: 'TWD', opening_balance: '0', balance: '0', balance_main: '0', entry_count: 1,
    group_id: 1, group_name: '現金', icon: '👛', color: null, is_archived: false, include_in_total: true,
    is_credit: false, closing_day: null, due_rule: null, due_value: null, credit_limit: null,
    combined_account_id: null, credit_sharing_id: null, auto_pay_account_id: null, fx_fee_pct: null,
    sort_order: 0, moze_id: null, available_credit: null, rule_summaries: [], settings_locally_edited: false,
    ...fields,
  } as unknown as LedgerAccount;
}

const CARD = { group_id: 3, group_name: '信用卡', icon: '💳', is_credit: true };
const ACCOUNTS = [
  account({ id: 1, name: '錢包', balance: '3070', balance_main: '3070' }),
  account({ id: 2, name: '日幣現金', currency: 'JPY', icon: '💴', balance: '53635', balance_main: '11156' }),
  account({ ...CARD, id: 10, name: '玉山信用卡', icon: '🏔', closing_day: 15, due_rule: 'days_after_closing', due_value: 20, credit_limit: '300000', available_credit: '251678', balance: '-5513', balance_main: '-5513' }),
  account({ ...CARD, id: 11, name: '玉山 Only', combined_account_id: 10, balance: '-4905', balance_main: '-4905', rule_summaries: ['一般 3.2%', '餐飲/國外 5.2%'] }),
  account({ ...CARD, id: 12, name: '玉山 UNI', combined_account_id: 10, balance: '-27218', balance_main: '-27218' }),
  account({ ...CARD, id: 13, name: '玉山 U Bear', combined_account_id: 10, balance: '-6726', balance_main: '-6726' }),
  account({ id: 20, name: 'Pi 拍錢包 P幣', group_id: 6, group_name: '紅利點數', icon: '⭐', include_in_total: false, balance: '2500', balance_main: '2500' }),
];

const LATEST = {
  id: 1, status: 'succeeded', started_at: '2026-10-02T03:33:00+08:00', finished_at: '2026-10-02T03:34:00+08:00',
  file_name: 'MOZE_4.0.zip', file_sha256: 'a'.repeat(64), row_count: 7553,
  summary: { kind: 'moze_backup', needs_review: { count: 2, reasons: {} } },
} as unknown as ImportRun;

describe('AccountingAccountsComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 10, 0));
    await TestBed.configureTestingModule({
      imports: [AccountingAccountsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  /**
   * Answers every open statement read (`BillingService`): summary spend per card id (default 0, i.e. nothing to pay)
   * and the payments made in its payment window (closing → day before the next closing, capped at today).
   */
  function flushBills(bills: Record<number, { spend: string; payments?: string[] }> = {}): void {
    for (const req of http.match(r => /^\/api\/accounting\/accounts\/\d+\/summary$/.test(r.url))) {
      const id = Number(req.request.url.split('/')[4]);
      const spend = bills[id]?.spend ?? '0';
      req.flush({
        account_id: id, currency: 'TWD', date_from: req.request.params.get('date_from'), date_to: req.request.params.get('date_to'),
        spend, income: '0', rewards: '0', net: spend, end_balance: spend, count: 1,
      });
    }
    for (const req of http.match(r => /^\/api\/accounting\/accounts\/\d+\/entries$/.test(r.url))) {
      const id = Number(req.request.url.split('/')[4]);
      expect(req.request.params.get('kind')).toBe('transfer_in');
      const payments = bills[id]?.payments ?? [];
      req.flush({
        items: payments.map((amount, index) => ({ id: 500 + index, kind: 'transfer_in', amount, account_id: id })),
        total: payments.length, limit: 100, offset: 0,
      });
    }
  }

  function render(
    accounts: LedgerAccount[],
    latest: ImportRun | null,
    bills: Record<number, { spend: string; payments?: string[] }> = {},
  ): ComponentFixture<AccountingAccountsComponent> {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    http.expectOne(r => r.url === '/api/accounting/accounts' && !r.urlWithParams.includes('include_archived=true')).flush(accounts);
    const latestReq = http.expectOne('/api/accounting/imports/latest');
    if (latest) {
      latestReq.flush(latest);
    } else {
      latestReq.flush({ detail: 'no import has run yet' }, { status: 404, statusText: 'Not Found' });
    }
    http.expectOne('/api/accounting/preference').flush({
      expense_income_colors: 'red_green', keypad_layout: 'calculator', week_start: 0, main_currency: 'TWD',
      hide_rewards_on_timeline: false, abbreviate_totals: true,
    });
    fixture.detectChanges();
    flushBills(bills);
    fixture.detectChanges();
    return fixture;
  }

  function rows(el: HTMLElement): HTMLElement[] {
    return Array.from(el.querySelectorAll<HTMLElement>('.acc-row'));
  }

  it('groups accounts in API order with main-currency subtotals', () => {
    const el = render(ACCOUNTS, LATEST).nativeElement as HTMLElement;

    const headers = Array.from(el.querySelectorAll('.grp:not(.archived-toggle)')).map(
      h => `${h.querySelector('span')?.textContent?.trim()} ${h.querySelector('b')?.textContent?.trim()}`,
    );
    expect(headers).toEqual(['現金 +$14,226', '信用卡 −$44,362', '紅利點數 +$2,500']);
    expect(el.querySelector('.partial-hint')).toBeNull();
    expect(rows(el)[1].querySelector('.meta')?.textContent?.trim()).toBe('JPY · 約 $11,156');
    expect(rows(el)[1].querySelector('.bal')?.textContent?.trim()).toBe('¥53,635');
    expect(rows(el)[0].getAttribute('href')).toBe('/accounting/accounts/1');
  });

  it('nests cards under their 主帳戶 with a badge and the family total', () => {
    const el = render(ACCOUNTS, LATEST).nativeElement as HTMLElement;
    const all = rows(el);
    const masterIndex = all.findIndex(r => r.querySelector('.name')?.textContent?.includes('玉山信用卡'));
    const master = all[masterIndex];

    expect(master.querySelector('.badge')?.textContent?.trim()).toBe('主帳戶 3');
    expect(master.querySelector('.bal')?.textContent?.trim()).toBe('−$44,362');
    expect(master.classList).not.toContain('sub');
    const children = all.slice(masterIndex + 1, masterIndex + 4);
    expect(children.map(r => r.querySelector('.name')?.textContent?.trim())).toEqual(['玉山 Only', '玉山 UNI', '玉山 U Bear']);
    expect(children.every(r => r.classList.contains('sub'))).toBe(true);
  });

  it('shows credit details and a due pill computed from the closing day', () => {
    const el = render(ACCOUNTS, LATEST).nativeElement as HTMLElement;
    const all = rows(el);
    const master = all.find(r => r.querySelector('.name')?.textContent?.includes('玉山信用卡'))!;
    const only = all.find(r => r.querySelector('.name')?.textContent?.trim() === '玉山 Only')!;

    expect(master.querySelector('.meta')?.textContent?.trim()).toBe('可用額度 $251,678');
    expect(master.querySelector('.due .pill')?.textContent?.trim()).toBe('結帳 10/15 · 繳款 11/04');
    expect(only.querySelector('.meta')?.textContent?.trim()).toBe('一般 3.2% · 餐飲/國外 5.2%');
    // The currency appears only when it is not the main currency.
    expect(all.find(r => r.textContent?.includes('Pi 拍錢包'))!.querySelector('.meta')?.textContent?.trim()).toBe('不納入總餘額');
    expect(rows(el)[0].querySelector('.meta')?.textContent?.trim()).toBe('');
  });

  describe('card due pills', () => {
    const master = (el: HTMLElement) => rows(el).find(r => r.querySelector('.name')?.textContent?.includes('玉山信用卡'))!;
    const UNPAID = { 10: { spend: '-12345.0000', payments: ['5000.0000'] } };

    it('shows 待繳帳款 and the due date of a statement with something left to pay', () => {
      const el = render(ACCOUNTS, LATEST, UNPAID).nativeElement as HTMLElement;
      expect(master(el).querySelector('.owed')?.textContent?.trim()).toBe('待繳帳款 $7,345');
      expect(master(el).querySelector('.due .pill')?.textContent?.trim()).toBe('10/05 繳費截止');
      expect(master(el).querySelector('.due .pill')?.classList).toContain('warn');
      // 可用額度 keeps its line.
      expect(master(el).querySelector('.meta')?.textContent?.trim()).toBe('可用額度 $251,678');
      // The 主帳戶's children (calendar-month statements here) have nothing left to pay: no 待繳帳款.
      expect(rows(el).find(r => r.querySelector('.name')?.textContent?.trim() === '玉山 Only')!.querySelector('.owed')).toBeNull();
    });

    it('says 明天 / 今天 繳費截止 and 已逾期 around the due date', () => {
      vi.setSystemTime(new Date(2026, 9, 4, 10, 0));
      let el = render(ACCOUNTS, LATEST, UNPAID).nativeElement as HTMLElement;
      expect(master(el).querySelector('.due .pill')?.textContent?.trim()).toBe('明天繳費截止');
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        imports: [AccountingAccountsComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
      });
      http = TestBed.inject(HttpTestingController);

      vi.setSystemTime(new Date(2026, 9, 5, 10, 0));
      el = render(ACCOUNTS, LATEST, UNPAID).nativeElement as HTMLElement;
      expect(master(el).querySelector('.due .pill')?.textContent?.trim()).toBe('今天繳費截止');
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        imports: [AccountingAccountsComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
      });
      http = TestBed.inject(HttpTestingController);

      vi.setSystemTime(new Date(2026, 9, 6, 10, 0));
      el = render(ACCOUNTS, LATEST, UNPAID).nativeElement as HTMLElement;
      const pill = master(el).querySelector('.due .pill')!;
      expect(pill.textContent?.trim()).toBe('已逾期');
      expect(pill.classList).toContain('overdue');
    });

    it('shows 待繳帳款 and the pills of a card whose statement is the calendar month', () => {
      const monthly = account({ ...CARD, id: 30, name: '月結卡', closing_day: null, due_rule: 'fixed_day', due_value: 5, balance: '-900' });
      const wallet = account({ id: 31, name: '現金袋', closing_day: null, due_rule: 'fixed_day', due_value: 5 });
      const el = render([wallet, monthly], LATEST, { 30: { spend: '-900.0000' } }).nativeElement as HTMLElement;
      const row = rows(el).find(r => r.querySelector('.name')?.textContent?.trim() === '月結卡')!;
      expect(row.querySelector('.owed')?.textContent?.trim()).toBe('待繳帳款 $900');
      expect(row.querySelector('.due .pill')?.textContent?.trim()).toBe('10/05 繳費截止');
      expect(rows(el).find(r => r.querySelector('.name')?.textContent?.trim() === '現金袋')!.querySelector('.due')).toBeNull();
    });

    it('shows the cycle pill of a paid card whose statement is the calendar month', () => {
      const monthly = account({ ...CARD, id: 30, name: '月結卡', closing_day: null, due_rule: 'fixed_day', due_value: 5 });
      const el = render([monthly], LATEST).nativeElement as HTMLElement;
      expect(rows(el)[0].querySelector('.due .pill')?.textContent?.trim()).toBe('結帳 10/31 · 繳款 11/05');
    });

    it('keeps the cycle pill and no 待繳帳款 for a paid statement', () => {
      const el = render(ACCOUNTS, LATEST, { 10: { spend: '-800.0000', payments: ['800.0000'] } }).nativeElement as HTMLElement;
      expect(master(el).querySelector('.owed')).toBeNull();
      expect(master(el).querySelector('.due .pill')?.textContent?.trim()).toBe('結帳 10/15 · 繳款 11/04');
    });

    it('re-reads the statement after an entry write', () => {
      const fixture = render(ACCOUNTS, LATEST, UNPAID);
      const el = fixture.nativeElement as HTMLElement;
      expect(master(el).querySelector('.owed')).not.toBeNull();
      TestBed.inject(AccountingService).deleteEntry(99).subscribe();
      http.expectOne(r => r.method === 'DELETE').flush(null);
      fixture.detectChanges();
      http.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts').flush(ACCOUNTS);
      http.expectOne('/api/accounting/imports/latest').flush(LATEST);
      fixture.detectChanges();
      flushBills({ 10: { spend: '-12345.0000', payments: ['12345.0000'] } });
      fixture.detectChanges();

      expect(master(el).querySelector('.owed')).toBeNull();
    });
  });

  it('shows the total, assets and liabilities of accounts included in the total', () => {
    const el = render(ACCOUNTS, LATEST).nativeElement as HTMLElement;

    expect(el.querySelector('.nw small')?.textContent?.trim()).toBe('總額 TWD');
    expect(el.querySelector('.total-amount')?.textContent?.trim()).toBe('−$30,136');
    expect(el.querySelector('.totals-line')?.textContent?.trim()).toBe('總資產 $14,226 · 總負債 $44,362');
    expect(el.querySelector('.add-account')?.getAttribute('href')).toBe('/accounting/accounts/new');
  });

  it('collapses a group from its header', () => {
    const fixture = render(ACCOUNTS, LATEST);
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('.grp')!.click();
    fixture.detectChanges();

    expect(rows(el).map(r => r.querySelector('.name')?.textContent?.trim())).not.toContain('錢包');
    expect(el.querySelector('.grp')?.getAttribute('aria-expanded')).toBe('false');
  });

  it('loads archived accounts into the collapsed 封存 section on demand', () => {
    const fixture = render(ACCOUNTS, LATEST);
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('.archived-toggle')!.click();
    http
      .expectOne(r => r.url === '/api/accounting/accounts' && r.urlWithParams.includes('include_archived=true'))
      .flush([...ACCOUNTS, account({ id: 30, name: '舊帳戶', is_archived: true, group_name: null })]);
    fixture.detectChanges();

    const archived = Array.from(el.querySelectorAll('.archived .acc-row')).map(r => r.querySelector('.name')?.textContent?.trim());
    expect(archived).toEqual(['舊帳戶']);
    expect(el.querySelector('.archived-toggle')?.textContent).toContain('封存 (1)');
  });

  it('applies only the latest archived-accounts answer', () => {
    const fixture = render(ACCOUNTS, LATEST);
    const el = fixture.nativeElement as HTMLElement;
    const toggle = () => {
      el.querySelector<HTMLButtonElement>('.archived-toggle')!.click();
      fixture.detectChanges();
    };
    toggle();
    toggle();
    toggle();
    const [first, second] = http.match(r => r.url === '/api/accounting/accounts' && r.urlWithParams.includes('include_archived=true'));
    second.flush([account({ id: 31, name: '新封存', is_archived: true })]);
    first.flush([account({ id: 30, name: '舊帳戶', is_archived: true })]);
    fixture.detectChanges();

    const archived = Array.from(el.querySelectorAll('.archived .acc-row')).map(r => r.querySelector('.name')?.textContent?.trim());
    expect(archived).toEqual(['新封存']);
  });

  it('keeps the latest import line with the review count', () => {
    const el = render(ACCOUNTS, LATEST).nativeElement as HTMLElement;
    const status = el.querySelector('.import-status')?.textContent ?? '';
    expect(status).toContain('成功');
    expect(status).toContain('待確認 2 筆');
  });

  it('links the empty state to the settings page', () => {
    const el = render([], null).nativeElement as HTMLElement;
    expect(el.querySelector('.empty-state')?.textContent).toContain('尚未匯入 MOZE 資料');
    expect(el.querySelector('.empty-state a')?.getAttribute('href')).toBe('/accounting/settings');
  });

  it('reloads the account list when accountsChanged bumps', () => {
    const fixture = render(ACCOUNTS, LATEST);
    const el = fixture.nativeElement as HTMLElement;
    const service = TestBed.inject(AccountingService);
    const before = service.accountsChanged();

    // Any successful account write bumps the signal (Task 18); here a rename from another pane.
    service.updateAccount(1, { name: '零錢包' } as AccountInput).subscribe();
    http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/1').flush({});
    expect(service.accountsChanged()).toBe(before + 1);
    fixture.detectChanges();

    // getPreference() is cached per session, so only the list and the latest import are fetched again.
    http
      .expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts' && !r.urlWithParams.includes('include_archived=true'))
      .flush([account({ id: 1, name: '零錢包', balance: '3070', balance_main: '3070' }), ...ACCOUNTS.slice(1)]);
    http.expectOne('/api/accounting/imports/latest').flush(LATEST);
    fixture.detectChanges();
    flushBills();

    expect(rows(el)[0].querySelector('.name')?.textContent?.trim()).toBe('零錢包');
  });

  it('reloads the balances after an entry write elsewhere (entriesChanged)', () => {
    const fixture = render(ACCOUNTS, LATEST);
    const el = fixture.nativeElement as HTMLElement;
    const service = TestBed.inject(AccountingService);

    service.deleteEntry(99).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/99').flush(null);
    fixture.detectChanges();

    http
      .expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts' && !r.urlWithParams.includes('include_archived=true'))
      .flush([account({ id: 1, name: '錢包', balance: '9999', balance_main: '9999' }), ...ACCOUNTS.slice(1)]);
    http.expectOne('/api/accounting/imports/latest').flush(LATEST);
    fixture.detectChanges();
    flushBills();

    expect(rows(el)[0].querySelector('.bal')?.textContent?.trim()).toBe('$9,999');
  });

  it('shows — for a foreign account without a main-currency rate and leaves it out of the sums', () => {
    const el = render([
      account({ id: 1, name: '錢包', balance: '3070', balance_main: '3070' }),
      account({ id: 5, name: '美金', currency: 'USD', icon: '💵', balance: '100', balance_main: null }),
    ], LATEST).nativeElement as HTMLElement;

    expect(rows(el)[1].querySelector('.meta')?.textContent?.trim()).toBe('USD · 約 —');
    expect(rows(el)[1].querySelector('.bal')?.textContent?.trim()).toBe('US$100');
    expect(el.querySelector('.total-amount')?.textContent?.trim()).toBe('$3,070');
    expect(el.querySelector('.grp b')?.textContent?.trim()).toBe('+$3,070');
  });

  it('reloads the list with the new main currency after a preference save', () => {
    const fixture = render(ACCOUNTS, LATEST);
    const el = fixture.nativeElement as HTMLElement;
    const service = TestBed.inject(AccountingService);
    const saved = {
      expense_income_colors: 'red_green', keypad_layout: 'calculator', week_start: 0, main_currency: 'JPY',
      hide_rewards_on_timeline: false, abbreviate_totals: true,
    } as never;
    service.updatePreference(saved).subscribe();
    http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference').flush(saved);
    fixture.detectChanges();

    http.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts').flush([
      account({ id: 1, name: '錢包', balance: '3070', balance_main: '14000' }),
    ]);
    http.expectOne('/api/accounting/imports/latest').flush(LATEST);
    fixture.detectChanges();
    flushBills();

    expect(el.querySelector('.nw small')?.textContent?.trim()).toBe('總額 JPY');
    expect(el.querySelector('.total-amount')?.textContent?.trim()).toBe('¥14,000');
  });

  it('shows — for a group without any converted balance and flags groups with unconverted accounts', () => {
    const el = render([
      account({ id: 1, name: '錢包', balance: '3070', balance_main: '3070' }),
      account({ id: 5, name: '美金', currency: 'USD', balance: '100', balance_main: null }),
      account({ id: 6, name: 'USDT', currency: 'USDT', group_id: 9, group_name: '加密貨幣', balance: '50', balance_main: null }),
    ], LATEST).nativeElement as HTMLElement;

    const headers = Array.from(el.querySelectorAll<HTMLElement>('.grp:not(.archived-toggle)'));
    expect(headers.map(h => h.querySelector('b')?.textContent?.trim())).toEqual(['+$3,070', '—']);
    expect(headers.map(h => h.querySelector('.partial-hint')?.textContent?.trim() ?? null)).toEqual(['部分帳戶未換算', '部分帳戶未換算']);
  });
});
