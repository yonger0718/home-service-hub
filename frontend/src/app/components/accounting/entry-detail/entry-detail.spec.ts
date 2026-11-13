import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, RouterLink, convertToParamMap, provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { BehaviorSubject, Subject, startWith } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { EntryDetailComponent, settledValue } from './entry-detail';

function entry(fields: Record<string, unknown>): LedgerEntry {
  return {
    id: 1, kind: 'expense', amount: '-100.0000', currency: 'TWD', original_amount: null, original_currency: null,
    fx_rate: null, fx_source: null, entry_date: '2026-10-01', entry_time: null, posted_date: '2026-10-01',
    account_id: 3, account_name: '玉山 UNI', category: null, category_id: null, category_icon: null, category_color: null,
    project: null, name: null, merchant: null, counterparty: null, counterparty_id: null, description: null, tags: [],
    parent_entry_id: null, transfer_group_id: null, group: null, rule_names: [], invoice_number: null,
    needs_review: false, source: 'manual', moze_id: null, locked: false, running_balance: '0',
    ...fields,
  } as unknown as LedgerEntry;
}

function detail(fields: Record<string, unknown>): EntryDetail {
  return {
    ...entry({}),
    children: [], group_members: [], transfer_counterpart: null, settles: null, settled_by: [], refunds: null,
    refunded_by: [], rules: [], rewards: [], accruals: [], open_amount: null, is_settled: null, refunded_amount: '0',
    ...fields,
  } as unknown as EntryDetail;
}

const ACCOUNTS = [
  { id: 3, name: '玉山 UNI', currency: 'TWD', is_archived: false, is_credit: true },
  { id: 7, name: '錢包', currency: 'TWD', is_archived: false },
  { id: 9, name: '日幣現金', currency: 'JPY', is_archived: false },
] as unknown as LedgerAccount[];

const RECEIVABLE = detail({
  id: 42, kind: 'receivable', amount: '-420.0000', name: '代付 晚餐', counterparty: 'Alan', counterparty_id: 5,
  category: '應收款項/代付', category_icon: '🤝', category_color: '#84dccf', open_amount: '220.0000', is_settled: false,
  settled_by: [entry({ id: 50, kind: 'receivable', amount: '200.0000', name: '收款', account_name: '錢包' })],
});

describe('EntryDetailComponent', () => {
  let http: HttpTestingController;
  let params: BehaviorSubject<ParamMap>;
  let query: ParamMap;
  /** A later query of the same route (the grid picking another day while the detail stays open). */
  let queryChanges: Subject<ParamMap>;

  beforeEach(async () => {
    params = new BehaviorSubject(convertToParamMap({ id: '42' }));
    query = convertToParamMap({});
    queryChanges = new Subject();
    await TestBed.configureTestingModule({
      imports: [EntryDetailComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        provideLocationMocks(),
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: params,
            get queryParamMap() { return queryChanges.pipe(startWith(query)); },
            snapshot: { get queryParamMap() { return query; } },
          },
        },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(body: EntryDetail, accounts: LedgerAccount[] = ACCOUNTS): ComponentFixture<EntryDetailComponent> {
    const fixture = TestBed.createComponent(EntryDetailComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(body);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(accounts);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<EntryDetailComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  it('uses line icons on the actions', () => {
    const icons: Record<string, string> = { edit: 'pencil', copy: 'copy', refund: 'undo', settle: 'check-circle', delete: 'trash' };
    // A receivable offers 結算 but no 退款; a plain expense offers 退款: check each action on the entry that shows it.
    const cases: [typeof RECEIVABLE, string[]][] = [
      [RECEIVABLE, ['edit', 'copy', 'settle', 'delete']],
      [detail({ id: 42, amount: '-500.0000', name: '外套' }), ['edit', 'copy', 'refund', 'delete']],
    ];
    for (const [d, actions] of cases) {
      const fixture = render(d);
      const root = el(fixture);
      for (const action of actions) {
        const i = root.querySelector(`.action-${action} i.pi-${icons[action]}`);
        expect(i, action).not.toBeNull();
        expect(i!.getAttribute('aria-hidden')).toBe('true');
      }
      const t = root.querySelector('nav.actions')!.textContent ?? '';
      expect(/[\u{1F300}-\u{1FAFF}]/u.test(t)).toBe(false);
      expect(/[✎⧉↺]/.test(t)).toBe(false);
    }
  });

  const accrual = (fields: Record<string, unknown>) => ({
    id: 1, rule_id: 101, rule_name: '測試規則', estimate: '10.0000', amount: '10.0000', state: 'estimated', post_on: null,
    ledger_entry_id: null, ledger_amount: null, reward_account_id: 3, reward_account_name: '玉山 UNI', ...fields,
  });
  const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim();
  const pointsAccount = (extra: Record<string, unknown> = {}) =>
    [...ACCOUNTS, { id: 11, name: '亞洲萬里通', currency: 'TWD', is_archived: false, is_credit: false, ...extra } as unknown as LedgerAccount];
  const pointsDetail = () => detail({
    id: 42, amount: '-500.0000', name: '外套',
    rewards: [entry({ id: 60, kind: 'reward', amount: '120.0000', name: '點數回饋', account_id: 11, account_name: '亞洲萬里通' })],
    accruals: [accrual({
      estimate: '120.0000', amount: '120.0000', state: 'posted', post_on: '2026-11-24', ledger_entry_id: 60,
      ledger_amount: '120.0000', reward_account_id: 11, reward_account_name: '亞洲萬里通',
    })],
  });

  it('shows one 回饋 section with a tagged card per accrual', () => {
    const fixture = render(detail({
      id: 42, amount: '-500.0000', name: '外套',
      accruals: [
        accrual({ id: 1, rule_name: '全聯回饋', estimate: '13.0000', amount: '13.0000', post_on: '2026-12-15', reward_account_name: '玉山 Pi 卡' }),
        accrual({ id: 2, rule_name: '上限規則', estimate: '64.0000', amount: '20.0000' }),
      ],
    }));
    const root = el(fixture);
    const section = root.querySelector('app-entry-reward-section')!;
    expect(Array.from(section.querySelectorAll('.dsec')).map(h => text(h))).toEqual(['回饋']);
    const cards = Array.from(section.querySelectorAll('.reward-card'));
    expect(cards.length).toBe(2);
    for (const card of cards) {
      expect(text(card.querySelector('app-reward-state-tag'))).toBe('預估');
    }
    expect(text(cards[0])).toContain('+$13');
    expect(text(cards[0])).toContain('預計 12/15 入帳到 玉山 Pi 卡');
    expect(text(cards[1])).toContain('+$20');
    expect(text(cards[1].querySelector('.reward-was'))).toBe('$64');
    expect(text(cards[1])).toContain('算出 $64，本期上限只剩 $20');
    expect(root.textContent).not.toContain('紅利回饋');
    expect(root.textContent).not.toContain('預估回饋');
  });

  it('links posted cards to their reward rows without a second list', () => {
    const fixture = render(detail({
      id: 42, amount: '-500.0000', name: '外套',
      rewards: [
        entry({ id: 501, kind: 'reward', amount: '10.0000', name: '回饋甲' }),
        entry({ id: 502, kind: 'reward', amount: '20.0000', name: '回饋乙' }),
      ],
      accruals: [
        accrual({ id: 1, state: 'posted', ledger_entry_id: 501, ledger_amount: '10.0000' }),
        accrual({ id: 2, estimate: '20.0000', amount: '20.0000', state: 'posted', ledger_entry_id: 502, ledger_amount: '20.0000' }),
      ],
    }));
    const root = el(fixture);
    expect(root.querySelectorAll('.reward-card').length).toBe(2);
    const links = Array.from(root.querySelectorAll<HTMLAnchorElement>('.reward-link'));
    expect(links.length).toBe(2);
    expect(links[0].getAttribute('href')).toMatch(/\/accounting\/entries\/501$/);
    expect(links[1].getAttribute('href')).toMatch(/\/accounting\/entries\/502$/);
    expect(root.textContent).not.toContain('紅利回饋');
  });

  it('links a bank_confirmed card to its reward row', () => {
    const fixture = render(detail({
      id: 42, amount: '-500.0000', name: '外套',
      rewards: [entry({ id: 501, kind: 'reward', amount: '10.0000', name: '回饋甲' })],
      accruals: [accrual({ id: 1, state: 'bank_confirmed', ledger_entry_id: 501, ledger_amount: '10.0000' })],
    }));
    const link = el(fixture).querySelector<HTMLAnchorElement>('.reward-card .reward-link')!;
    expect(link.getAttribute('href')).toMatch(/\/accounting\/entries\/501$/);
  });

  it('prints points with the reward account name, never $', () => {
    const fixture = render(pointsDetail(), pointsAccount());
    const card = text(el(fixture).querySelector('.reward-card'))!;
    expect(card).toContain('120 亞洲萬里通');
    expect(card).not.toContain('$120');
  });

  it('prints a cash-equivalent points reward as money', () => {
    const fixture = render(pointsDetail(), pointsAccount({ cash_equivalent: true }));
    const card = text(el(fixture).querySelector('.reward-card'))!;
    expect(card).toContain('+$120');
    expect(card).not.toContain('120 亞洲萬里通');
  });

  it('keeps points when the API sends no cash_equivalent', () => {
    const fixture = render(pointsDetail(), pointsAccount());
    expect(text(el(fixture).querySelector('.reward-amount'))).toBe('+120 亞洲萬里通');
  });

  it('shows an imported reward row as a posted card', () => {
    const fixture = render(detail({
      id: 42, amount: '-500.0000', name: '外套',
      rewards: [entry({ id: 60, kind: 'reward', amount: '35.0000', name: '匯入回饋', posted_date: '2020-01-05' })],
    }));
    const cards = el(fixture).querySelectorAll('.reward-card');
    expect(cards.length).toBe(1);
    expect(text(cards[0].querySelector('app-reward-state-tag'))).toBe('已入帳');
    expect(text(cards[0])).toContain('+$35');
    expect(text(cards[0])).toContain('01/05');
  });

  it('keeps the reprice card as the section\'s last item and reloads after 重算 (#306)', () => {
    const fixture = render(detail({
      id: 42, amount: '-2000.0000', name: '外套', needs_review: true, review_reason: 'reward_posted',
      accruals: [accrual({ state: 'posted', post_on: '2026-11-24', ledger_entry_id: 60, ledger_amount: '10.0000', reward_account_id: 11, reward_account_name: '測試點數' })],
    }), [...ACCOUNTS, { id: 11, name: '測試點數', currency: 'TWD', is_archived: false, is_credit: false } as unknown as LedgerAccount]);
    http.expectOne('/api/accounting/entries/42/reward-reprice').flush({
      entry_id: 42, flagged: true, applied: [],
      items: [{ accrual_id: 1, rule_id: 101, rule_name: '測試規則', reward_entry_id: 60, booked: '10.0000', new: '20.0000', status: 'reprice' }],
    });
    fixture.detectChanges();
    const section = el(fixture).querySelector('app-entry-reward-section')!;
    const last = section.lastElementChild!;
    expect(last.matches('app-entry-reward-reprice-card') || last.querySelector('app-entry-reward-reprice-card') !== null).toBe(true);
    const card = el(fixture).querySelector('app-entry-reward-reprice-card')!;
    expect(card.textContent?.replace(/\s+/g, ' ')).toContain('已入帳 10 測試點數 → 新算 20 測試點數');
    (card.querySelector('.reprice-apply') as HTMLButtonElement).click();
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries/42/reward-reprice')
      .flush({ entry_id: 42, flagged: false, applied: [1], items: [] });
    http.expectOne('/api/accounting/entries/42').flush(detail({ id: 42, amount: '-2000.0000', name: '外套', review_reason: null }));
    fixture.detectChanges();
    expect(el(fixture).querySelector('.reprice-card')).toBeNull();
  });

  it('keeps the reprice card for a reward_posted entry without accruals or rows', () => {
    const fixture = render(detail({ id: 42, amount: '-2000.0000', name: '外套', needs_review: true, review_reason: 'reward_posted' }));
    http.expectOne('/api/accounting/entries/42/reward-reprice').flush({
      entry_id: 42, flagged: true, applied: [],
      items: [{ accrual_id: 1, rule_id: 101, rule_name: '測試規則', reward_entry_id: 60, booked: '10.0000', new: '20.0000', status: 'reprice' }],
    });
    fixture.detectChanges();
    const section = el(fixture).querySelector('app-entry-reward-section')!;
    expect(section.querySelector('app-entry-reward-reprice-card')).not.toBeNull();
    expect(section.querySelectorAll('.reward-card').length).toBe(0);
  });

  it('strikes the expected figure on a forfeited card', () => {
    const fixture = render(detail({
      id: 42, amount: '-500.0000', name: '外套',
      accruals: [accrual({ state: 'forfeited', estimate: '13.0000', amount: '13.0000' })],
    }));
    const amount = el(fixture).querySelector('.reward-card .reward-amount')!;
    expect(amount.tagName).toBe('S');
    expect(text(amount)).toBe('$13');
    expect(text(el(fixture).querySelector('.reward-card'))).toContain('回饋記錄已刪除，不會入帳');
  });

  it('has no 回饋 section without accruals, reward rows or a reward_posted review', () => {
    const fixture = render(detail({ id: 42, amount: '-500.0000', name: '外套' }));
    const section = el(fixture).querySelector('app-entry-reward-section')!;
    expect(section.querySelector('.dsec')).toBeNull();
    expect(section.querySelector('.reward-card')).toBeNull();
  });

  it('places the section after the debt state and before the e-invoice card', () => {
    const fixture = render(detail({
      ...RECEIVABLE, accruals: [accrual({})],
      einvoice: { id: 1, number: 'AB12345678', issue_date: '2026-10-01', seller_name: '測試商店', total_amount: '500', linked_by: 'owner', linked_to: 'entry', items: [] },
    }));
    const root = el(fixture);
    const debt = root.querySelector('.open-state')!;
    const section = root.querySelector('app-entry-reward-section')!;
    const invoice = root.querySelector('app-entry-einvoice-card')!;
    expect(debt.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(section.compareDocumentPosition(invoice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the open amount, the collection and an enabled 新增收款 on a receivable', () => {
    const fixture = render(RECEIVABLE);

    expect(el(fixture).querySelector('.open-amount')?.textContent?.trim()).toBe('剩餘 $220');
    expect(el(fixture).querySelector('.related')?.textContent).toContain('收款');
    const settle = el(fixture).querySelector<HTMLButtonElement>('.action-settle')!;
    expect(settle.textContent).toContain('新增收款');
    expect(settle.disabled).toBe(false);
    expect(el(fixture).querySelector('.dhead')?.getAttribute('style')).toContain('background');
    expect(el(fixture).querySelector('.dgrid')?.textContent).toContain('Alan');
  });

  it('breaks a partly collected receivable down into 原始 · 已收 · 剩餘', () => {
    const fixture = render(RECEIVABLE);
    expect(el(fixture).querySelector('.breakdown')?.textContent?.trim()).toBe('原始 $420 · 已收 $200 · 剩餘 $220');
    // Above the settlements list.
    const related = el(fixture).querySelector('.related')!;
    expect(el(fixture).querySelector('.breakdown')!.compareDocumentPosition(related) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('breaks a partly repaid payable down into 原始 · 已還 · 剩餘', () => {
    const payable = render({
      ...RECEIVABLE, kind: 'payable', amount: '300.0000', open_amount: '100.0000',
      settled_by: [entry({ id: 51, kind: 'payable', amount: '-200.0000', name: '還款' })],
    });
    expect(el(payable).querySelector('.breakdown')?.textContent?.trim()).toBe('原始 $300 · 已還 $200 · 剩餘 $100');
  });

  it('sums a cross-currency collection as its original amount in the debt currency (#382)', () => {
    const fixture = render(detail({
      ...RECEIVABLE, currency: 'USD', amount: '-20.0000', open_amount: '0.0000', is_settled: true,
      settled_by: [entry({
        id: 60, kind: 'receivable', amount: '640.0000', currency: 'TWD', original_amount: '20.0000',
        original_currency: 'USD', name: '收款', account_name: '錢包',
      })],
    }));
    expect(el(fixture).querySelector('.breakdown')?.textContent?.trim()).toBe('原始 US$20 · 已收 US$20 · 剩餘 US$0');
    expect(el(fixture).querySelector('.settled')?.textContent?.trim()).toBe('已結清');
  });

  it('does not count a legacy other-currency settlement without an original amount (#382)', () => {
    const fixture = render(detail({
      ...RECEIVABLE, amount: '-100.0000', is_closed: true, open_amount: '100.0000', is_settled: false,
      settled_by: [entry({ id: 61, kind: 'receivable', amount: '100.0000', currency: 'JPY', name: '收款' })],
    }));
    expect(el(fixture).querySelector('.breakdown')?.textContent?.trim()).toBe('原始 $100 · 已收 $0 · 已結清');
  });

  it('shows no breakdown before anything was collected', () => {
    const fixture = render({ ...RECEIVABLE, open_amount: '420.0000', settled_by: [] });
    expect(el(fixture).querySelector('.breakdown')).toBeNull();
    expect(el(fixture).querySelector('.open-amount')?.textContent?.trim()).toBe('剩餘 $420');
  });

  it('shows 已結清 and no 新增收款 on a debt MOZE closed with a partial settlement', () => {
    const fixture = render({ ...RECEIVABLE, is_closed: true, open_amount: '220.0000', is_settled: false });

    expect(el(fixture).querySelector('.settled')?.textContent?.trim()).toBe('已結清');
    expect(el(fixture).querySelector('.open-amount')).toBeNull();
    expect(el(fixture).querySelector('.action-settle')).toBeNull();
    // What was actually collected (the settlements), then 已結清 for the rest MOZE wrote off.
    expect(el(fixture).querySelector('.breakdown')?.textContent?.trim()).toBe('原始 $420 · 已收 $200 · 已結清');
    // The collection already made stays listed.
    expect(el(fixture).querySelector('.related')?.textContent).toContain('收款');
  });

  it('settles from the detail and then shows 已結清', () => {
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
    fixture.detectChanges();

    const options = Array.from(el(fixture).querySelectorAll('.form-account option')).map(o => o.textContent?.trim());
    expect(options).toEqual(['玉山 UNI', '錢包', '日幣現金 (JPY)']);
    const account = el(fixture).querySelector<HTMLSelectElement>('.form-account')!;
    account.value = '7';
    account.dispatchEvent(new Event('change'));
    const amount = el(fixture).querySelector<HTMLInputElement>('.form-amount')!;
    amount.value = '220';
    amount.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();

    const req = http.expectOne('/api/accounting/entries/42/settle');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toMatchObject({ account_id: 7, amount: '220', entry_time: null, description: null });
    req.flush({});
    http.expectOne('/api/accounting/entries/42').flush({ ...RECEIVABLE, open_amount: '0.0000', is_settled: true });
    fixture.detectChanges();

    expect(el(fixture).querySelector('.settled')?.textContent?.trim()).toBe('已結清');
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.disabled).toBe(true);
  });

  it('refuses a settlement above the open amount without calling the API', () => {
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
    fixture.detectChanges();
    const amount = el(fixture).querySelector<HTMLInputElement>('.form-amount')!;
    amount.value = '300';
    amount.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();
    fixture.detectChanges();

    expect(el(fixture).querySelector('.form-error')?.textContent).toContain('$220');
  });

  it('lists the other transfer leg in the delete confirmation and deletes', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(detail({
      id: 42, kind: 'transfer_out', amount: '-10000.0000', transfer_group_id: 'g-1', account_name: '國泰主帳戶',
      transfer_counterpart: entry({ id: 43, kind: 'transfer_in', amount: '46200.0000', currency: 'JPY', account_name: '日幣現金' }),
    }));
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();

    const confirm = el(fixture).querySelector('.delete-confirm')!;
    expect(confirm.textContent).toContain('將一併刪除');
    expect(confirm.textContent).toContain('日幣現金');
    expect(confirm.textContent).toContain('¥46,200');

    confirm.querySelector<HTMLButtonElement>('.delete-entry')!.click();
    const req = http.expectOne('/api/accounting/entries/42');
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    // Opened directly (no in-app history): back to the timeline.
    expect(navigate).toHaveBeenCalledWith('/accounting');
  });

  it('shows the FX line and the fee inside the total', () => {
    const fixture = render(detail({
      id: 42, amount: '-1166.0000', original_amount: '-5390.0000', original_currency: 'JPY', fx_rate: '0.2163000000',
      fx_source: 'fx_api', name: 'Uniqlo 外套', category: '購物/衣物',
      children: [entry({ id: 44, kind: 'fee', amount: '-17.0000', name: '國外交易手續費' })],
    }));

    expect(el(fixture).querySelector('.detail-total')?.textContent?.trim()).toBe('−$1,183');
    expect(el(fixture).querySelector('.fee-note')?.textContent?.trim()).toBe('內含手續費 $17');
    expect(el(fixture).querySelector('.fx-line')?.textContent?.trim()).toBe('¥5,390 × 0.2163 = $1,166（線上匯率）');
    expect(el(fixture).querySelector('.dhead .cat')?.textContent?.trim()).toBe('購物 › 衣物');
    expect(el(fixture).querySelector('.related')?.textContent).toContain('手續費 · 國外交易手續費');
  });

  it('disables the write actions on a locked entry but keeps 複製', () => {
    const fixture = render(detail({ id: 42, source: 'moze_backup', moze_id: 'R-1', locked: true }));

    expect(el(fixture).querySelector('app-lock-banner')).not.toBeNull();
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-edit')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-refund')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-copy')!.disabled).toBe(false);
  });

  it('treats a 收款 row as a settlement: no 新增收款, 編輯 disabled, 對象 shown', () => {
    const fixture = render(detail({
      id: 42, kind: 'receivable', amount: '200.0000', is_settlement: true, counterparty: 'Alan', open_amount: null,
    }));

    expect(el(fixture).querySelector('.action-settle')).toBeNull();
    expect(el(fixture).querySelector('.open-state')).toBeNull();
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-edit')!.disabled).toBe(true);
    expect(el(fixture).querySelector('.dgrid')?.textContent).toContain('Alan');
  });

  it('drops a stale load when a newer one has started', () => {
    const fixture = render(RECEIVABLE);
    fixture.componentInstance.load(43);
    fixture.componentInstance.load(44);
    http.expectOne('/api/accounting/entries/44').flush(detail({ id: 44, name: '新的' }));
    http.expectOne('/api/accounting/entries/43').flush(detail({ id: 43, name: '舊的' }));
    fixture.detectChanges();

    expect(el(fixture).querySelector('.detail-name')?.textContent?.trim()).toBe('新的');
  });
  function openForm(fixture: ComponentFixture<EntryDetailComponent>, action: string, amount: string): void {
    el(fixture).querySelector<HTMLButtonElement>(action)!.click();
    fixture.detectChanges();
    const input = el(fixture).querySelector<HTMLInputElement>('.form-amount')!;
    input.value = amount;
    input.dispatchEvent(new Event('input'));
  }

  it('drops a late settle response once another entry is shown', () => {
    const fixture = render(RECEIVABLE);
    openForm(fixture, '.action-settle', '220');
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();
    const settle = http.expectOne('/api/accounting/entries/42/settle');

    params.next(convertToParamMap({ id: '43' }));
    http.expectOne('/api/accounting/entries/43').flush(detail({ id: 43, name: '午餐' }));
    settle.flush({});
    fixture.detectChanges();

    http.expectNone('/api/accounting/entries/42');
    expect(el(fixture).querySelector('.detail-name')?.textContent?.trim()).toBe('午餐');
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.disabled).toBe(false);
  });

  it('does not navigate after a delete when the owner has moved to another entry', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.delete-entry')!.click();
    const remove = http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/42');

    params.next(convertToParamMap({ id: '43' }));
    http.expectOne('/api/accounting/entries/43').flush(detail({ id: 43 }));
    remove.flush(null, { status: 204, statusText: 'No Content' });

    expect(navigate).not.toHaveBeenCalled();
  });

  it('clears the previous entry while the next one loads, so no write targets it', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(RECEIVABLE);
    const component = fixture.componentInstance;

    params.next(convertToParamMap({ id: '43' }));
    const next = http.expectOne('/api/accounting/entries/43');
    fixture.detectChanges();
    expect(el(fixture).querySelector('.detail-name')).toBeNull();
    expect(el(fixture).querySelector('.action-delete')).toBeNull();
    component.openPanel('delete');
    component.confirmDelete('entry');
    component.submitForm();
    http.expectNone(r => r.method !== 'GET');

    next.flush(detail({ id: 43, name: '午餐' }));
    fixture.detectChanges();
    expect(el(fixture).querySelector('.detail-name')?.textContent?.trim()).toBe('午餐');
    component.confirmDelete('entry');
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/43')
      .flush(null, { status: 204, statusText: 'No Content' });
    expect(navigate).toHaveBeenCalledWith('/accounting');
  });

  it('keeps the shown record when a reload of the same entry fails', () => {
    const fixture = render(RECEIVABLE);
    params.next(convertToParamMap({ id: '42' }));
    http.expectOne('/api/accounting/entries/42').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el(fixture).querySelector('.load-error')).not.toBeNull();
    expect(el(fixture).querySelector('.detail-name')?.textContent?.trim()).toBe('代付 晚餐');
  });

  it('returns to the passbook after a delete opened from it, and goes back when there is history', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    params.next(convertToParamMap({ id: '5', eid: '42' }));
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.delete-entry')!.click();
    http.expectOne(r => r.method === 'DELETE').flush(null, { status: 204, statusText: 'No Content' });
    expect(navigate).toHaveBeenCalledWith('/accounting/accounts/5');
    expect(back).not.toHaveBeenCalled();

    // In-app history: the router has a previous navigation.
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(back).toHaveBeenCalledTimes(1);
  });

  it('shows ✕ on the load-error page and leaves it for the parent list', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = TestBed.createComponent(EntryDetailComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush({ detail: 'gone' }, { status: 404, statusText: 'Not Found' });
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(ACCOUNTS);
    fixture.detectChanges();

    expect(el(fixture).querySelector('.load-error')).not.toBeNull();
    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(navigate).toHaveBeenCalledWith('/accounting');
  });

  it('closes to the parent list (not back) when opened with closeTo: list, after ✕ and after a delete', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    TestBed.inject(Location).go('/accounting/entries/42', '', { closeTo: 'list' });
    const fixture = render(RECEIVABLE);

    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(navigate).toHaveBeenLastCalledWith('/accounting');

    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.delete-entry')!.click();
    http.expectOne(r => r.method === 'DELETE').flush(null, { status: 204, statusText: 'No Content' });
    expect(navigate).toHaveBeenCalledTimes(2);
    expect(navigate).toHaveBeenLastCalledWith('/accounting');
    expect(back).not.toHaveBeenCalled();
  });

  it('closes to the selected calendar day with no history', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    query = convertToParamMap({ month: '2026-10', day: '2026-10-21' });
    TestBed.inject(Location).go('/accounting/entries/42', '', { closeTo: 'list' });
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(navigate).toHaveBeenLastCalledWith('/accounting?month=2026-10&day=2026-10-21');
  });

  it('keeps the query on 編輯', () => {
    query = convertToParamMap({ month: '2026-10', day: '2026-10-21' });
    const fixture = render(RECEIVABLE);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    fixture.componentInstance.editOne();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 42, 'edit'], { queryParamsHandling: 'preserve' });
  });

  it('✕ on the calendar goes back (history not grown), then the list it lands on shows the day in the URL', async () => {
    const router = TestBed.inject(Router);
    router.resetConfig([{ path: '**', children: [] }]);
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    // The grid moved the day while the detail was open: the route's query is the day now shown.
    query = convertToParamMap({ month: '2026-10', day: '2026-10-22' });
    const fixture = render(RECEIVABLE);
    const navigate = vi.spyOn(router, 'navigateByUrl');
    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(back).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();

    // The browser lands on the day the detail was opened from; the day shown now replaces it in place.
    await router.navigateByUrl('/accounting?month=2026-10&day=2026-10-21');
    await vi.waitFor(() => expect(router.url).toBe('/accounting?month=2026-10&day=2026-10-22'));
  });

  it('✕ on a detail reached from 清單 with only ?month goes back as before', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    query = convertToParamMap({ month: '2026-10' });
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(back).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('✕ off the calendar still goes back in history', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    query = convertToParamMap({ q: '晚餐' });
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(back).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('複製 keeps the calendar month and day', () => {
    query = convertToParamMap({ month: '2026-10', day: '2026-10-21' });
    const fixture = render(RECEIVABLE);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    el(fixture).querySelector<HTMLButtonElement>('.action-copy')!.click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { copy: 42, month: '2026-10', day: '2026-10-21' } });
  });

  it('複製 off the calendar opens the form with the copy only', () => {
    const fixture = render(RECEIVABLE);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    el(fixture).querySelector<HTMLButtonElement>('.action-copy')!.click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { copy: 42 } });
  });

  it('closes to the passbook when opened from it with closeTo: list', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    TestBed.inject(Location).go('/accounting/accounts/5/entries/42', '', { closeTo: 'list' });
    params.next(convertToParamMap({ id: '5', eid: '42' }));
    const fixture = render(RECEIVABLE);

    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(navigate).toHaveBeenCalledWith('/accounting/accounts/5');
  });

  it('closes to the reminder centre when opened from it, after ✕ and after a delete', () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    TestBed.inject(Location).go('/accounting/entries/42', '', { closeTo: 'reminders' });
    const fixture = render(RECEIVABLE);

    el(fixture).querySelector<HTMLButtonElement>('.close')!.click();
    expect(navigate).toHaveBeenLastCalledWith('/accounting/reminders');

    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.delete-entry')!.click();
    http.expectOne(r => r.method === 'DELETE').flush(null, { status: 204, statusText: 'No Content' });
    expect(navigate).toHaveBeenLastCalledWith('/accounting/reminders');
    expect(back).not.toHaveBeenCalled();
  });

  it('clears the record when the reload after a successful write fails', () => {
    const fixture = render(RECEIVABLE);
    openForm(fixture, '.action-settle', '220');
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();
    http.expectOne('/api/accounting/entries/42/settle').flush({});
    http.expectOne('/api/accounting/entries/42').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el(fixture).querySelector('.load-error')).not.toBeNull();
    expect(el(fixture).querySelector('.detail-name')).toBeNull();
  });

  it('refunds an expense into a same-currency account', () => {
    const fixture = render(detail({ id: 42, amount: '-500.0000', name: '外套', refunded_amount: '100.0000' }));
    openForm(fixture, '.action-refund', '300');
    expect(el(fixture).querySelector('.inline-form small')?.textContent).toContain('$400');
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();

    const req = http.expectOne('/api/accounting/entries/42/refund');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toMatchObject({ account_id: 3, amount: '300' });
    req.flush({});
    http.expectOne('/api/accounting/entries/42').flush(detail({ id: 42, amount: '-500.0000', refunded_amount: '400.0000' }));
    fixture.detectChanges();
    expect(el(fixture).querySelector('.inline-form')).toBeNull();
  });

  it('records a 還款 on a payable', () => {
    const fixture = render(detail({
      id: 42, kind: 'payable', amount: '300.0000', counterparty: 'Ben', open_amount: '300.0000', is_settled: false,
    }));
    expect(el(fixture).querySelector('.action-settle')?.textContent).toContain('新增還款');
    openForm(fixture, '.action-settle', '300');
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();

    const req = http.expectOne('/api/accounting/entries/42/settle');
    expect(req.request.body).toMatchObject({ account_id: 3, amount: '300' });
    req.flush({});
    http.expectOne('/api/accounting/entries/42').flush(detail({ id: 42, kind: 'payable', amount: '300.0000', open_amount: '0', is_settled: true }));
    fixture.detectChanges();
    expect(el(fixture).querySelector('.settled')?.textContent?.trim()).toBe('已結清');
  });

  it('deletes a whole split group', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(detail({
      id: 42,
      group: { id: 4, kind: 'split', name: '聚餐', merchant: null, description: null, count: 2, total: '-300.0000', currency: 'TWD' },
      group_members: [entry({ id: 42 }), entry({ id: 43, name: '代墊', amount: '-100.0000' })],
    }));
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    expect(el(fixture).querySelector('.delete-confirm')?.textContent).toContain('整組「聚餐」共 2 筆');

    el(fixture).querySelector<HTMLButtonElement>('.delete-group')!.click();
    const req = http.expectOne('/api/accounting/splits/4');
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(navigate).toHaveBeenCalledWith('/accounting');
  });
  function key(target: Element, init: KeyboardEventInit): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    return event;
  }

  it('closes an open inline form on Esc before anything else, and submits it on ⏎ (not on an IME commit)', () => {
    const fixture = render(RECEIVABLE);
    openForm(fixture, '.action-settle', '220');
    fixture.detectChanges();

    const esc = key(el(fixture).querySelector('.form-amount')!, { key: 'Escape' });
    fixture.detectChanges();
    expect(esc.defaultPrevented).toBe(true);
    expect(el(fixture).querySelector('.inline-form')).toBeNull();

    openForm(fixture, '.action-settle', '220');
    fixture.detectChanges();
    key(el(fixture).querySelector('.form-amount')!, { key: 'Enter', keyCode: 229 });
    http.expectNone('/api/accounting/entries/42/settle');
    const enter = key(el(fixture).querySelector('.form-amount')!, { key: 'Enter' });
    expect(enter.defaultPrevented).toBe(true);
    http.expectOne('/api/accounting/entries/42/settle').flush({});
    http.expectOne('/api/accounting/entries/42').flush(RECEIVABLE);
  });

  it('leaves Esc to the layout when no inline form is open', () => {
    const fixture = render(RECEIVABLE);
    const esc = key(el(fixture).querySelector('.entry-detail')!, { key: 'Escape' });
    expect(esc.defaultPrevented).toBe(false);
  });

  it("hides its own ✕ inside the layout's sheet", () => {
    TestBed.inject(LayoutModeService).set('sheet');
    const fixture = render(RECEIVABLE);
    expect(el(fixture).querySelector('.close')).toBeNull();
  });
  it('splits 類型 into 交易類型 and 事件類型 and colours the header text for contrast', () => {
    const fixture = render(RECEIVABLE);
    const cells = Array.from(el(fixture).querySelectorAll('.dgrid > div')).map(cell => [
      cell.querySelector('small')?.textContent?.trim(),
      cell.querySelector('b')?.textContent?.trim(),
    ]);
    expect(cells).toContainEqual(['交易類型', '應收款項']);
    expect(cells).toContainEqual(['事件類型', '單次']);
    expect(cells.some(([label]) => label === '類型')).toBe(false);
    const head = el(fixture).querySelector<HTMLElement>('.dhead')!;
    expect(['rgb(29, 28, 26)', '#1d1c1a']).toContain(head.style.color);

    const component = fixture.componentInstance;
    expect(component.eventKindLabel({ ...RECEIVABLE, schedule: { kind: 'recurring' } } as unknown as EntryDetail)).toBe('週期');
    expect(component.eventKindLabel({ ...RECEIVABLE, schedule: { kind: 'installment' } } as unknown as EntryDetail)).toBe('分期');
  });


  const SPLIT = { id: 4, kind: 'split', name: '旅遊', merchant: '商家', description: '備註', count: 2, total: '-60', currency: 'TWD' };

  /** A child of a two-member split (7 and 42); 42 is the opened one. */
  function splitChild(fields: Record<string, unknown> = {}): EntryDetail {
    return detail({
      id: 42, amount: '40', group: SPLIT,
      group_members: [
        { ...entry({ id: 7, amount: '-100', group: SPLIT }), protected: false, protected_reason: null },
        { ...entry({ id: 42, amount: '40', group: SPLIT }), protected: false, protected_reason: null },
      ],
      ...fields,
    });
  }

  /** The navigation state the detail was opened with (as `router.currentNavigation()` would report it). */
  function openedWith(state: Record<string, unknown>): void {
    vi.spyOn(TestBed.inject(Router), 'currentNavigation').mockReturnValue({ extras: { state } } as never);
  }

  function links(fixture: ComponentFixture<EntryDetailComponent>): RouterLink[] {
    return fixture.debugElement.queryAll(By.directive(RouterLink)).map(debug => debug.injector.get(RouterLink));
  }

  it('has no 多類別 parent card on a split child; a protected child still edits its metadata with itself selected', () => {
    const fixture = render(splitChild({
      kind: 'receivable', is_settlement: true,
      group_members: [
        { ...entry({ id: 7, amount: '-100', group: SPLIT }), protected: false, protected_reason: null },
        { ...entry({ id: 42, kind: 'receivable', is_settlement: true, amount: '40', group: SPLIT }), protected: true, protected_reason: 'settlement' },
      ],
    }));
    expect(el(fixture).querySelector('.split-parent-card')).toBeNull();
    expect(el(fixture).querySelector('app-split-folder')).toBeNull();
    expect(el(fixture).querySelectorAll('.split-child')).toHaveLength(0);
    expect(el(fixture).querySelector('.related')?.textContent ?? '').not.toContain('拆帳');
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    fixture.componentInstance.edit();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 42, 'edit'], { queryParamsHandling: 'preserve' });
  });

  it('draws a slim 「← 多類別 $合計」 link that replaces the child with its group view, carrying the group state', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    const fixture = render(splitChild());
    const back = el(fixture).querySelector<HTMLAnchorElement>('.group-back')!;
    expect(back.textContent?.replace(/\s+/g, ' ').trim()).toBe('← 多類別 −$60');
    expect(back.getAttribute('href')).toBe('/accounting/entries/42/group');
    const link = links(fixture).find(item => item.href === '/accounting/entries/42/group')!;
    expect(link.replaceUrl).toBe(true);
    expect(link.state).toEqual({ groupDepth: 2, viaGroup: true });
    // Above the coloured head.
    expect(back.compareDocumentPosition(el(fixture).querySelector('.dhead')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the calendar month and day on the 「← 多類別」 link and the related lines, and follows a day picked later', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    query = convertToParamMap({ month: '2026-10', day: '2026-10-21' });
    const fixture = render(splitChild({
      kind: 'expense', amount: '-60', refunded_amount: '0',
      refunded_by: [entry({ id: 7, kind: 'income', amount: '-100', group: SPLIT })],
      settled_by: [],
      children: [entry({ id: 90, kind: 'fee', amount: '-5' })],
    }));
    const hrefs = () => [
      el(fixture).querySelector('.group-back')?.getAttribute('href'),
      ...Array.from(el(fixture).querySelectorAll('.related a.dline')).map(line => line.getAttribute('href')),
    ];
    expect(hrefs()).toEqual([
      '/accounting/entries/42/group?month=2026-10&day=2026-10-21',
      '/accounting/entries/90?month=2026-10&day=2026-10-21',
      '/accounting/entries/7?month=2026-10&day=2026-10-21',
    ]);

    // The grid moves the day while the detail stays open (the route's query changes, the page is reused).
    query = convertToParamMap({ month: '2026-10', day: '2026-10-23' });
    queryChanges.next(query);
    fixture.detectChanges();
    expect(hrefs()).toEqual([
      '/accounting/entries/42/group?month=2026-10&day=2026-10-23',
      '/accounting/entries/90?month=2026-10&day=2026-10-23',
      '/accounting/entries/7?month=2026-10&day=2026-10-23',
    ]);
  });

  it('leaves the 「← 多類別」 link and the related lines without a query off the calendar', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    const fixture = render(splitChild({
      kind: 'expense', amount: '-60', refunded_amount: '0',
      refunded_by: [entry({ id: 7, kind: 'income', amount: '-100', group: SPLIT })],
      settled_by: [],
    }));
    expect(el(fixture).querySelector('.group-back')?.getAttribute('href')).toBe('/accounting/entries/42/group');
    expect(Array.from(el(fixture).querySelectorAll('.related a.dline')).map(line => line.getAttribute('href'))).toEqual(['/accounting/entries/7']);
  });

  it('keeps the passbook in the 「← 多類別」 link of a child opened from a passbook', () => {
    params.next(convertToParamMap({ id: '5', eid: '42' }));
    const fixture = render(splitChild());
    expect(el(fixture).querySelector('.group-back')?.getAttribute('href')).toBe('/accounting/accounts/5/entries/42/group');
  });

  it('has no 「← 多類別」 link on an entry outside a split', () => {
    const fixture = render(RECEIVABLE);
    expect(el(fixture).querySelector('.group-back')).toBeNull();
  });

  it('replaces the child in history when a related line leads to a sibling; other related lines push', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    const fixture = render(splitChild({
      kind: 'expense', amount: '-60', refunded_amount: '0',
      refunded_by: [entry({ id: 7, kind: 'income', amount: '-100', group: SPLIT })],
      settled_by: [],
      children: [entry({ id: 90, kind: 'fee', amount: '-5' })],
    }));
    const sibling = links(fixture).find(item => item.href === '/accounting/entries/7')!;
    expect(sibling.replaceUrl).toBe(true);
    expect(sibling.state).toEqual({ groupDepth: 2, viaGroup: true });
    const fee = links(fixture).find(item => item.href === '/accounting/entries/90')!;
    expect(fee.replaceUrl).toBe(false);
    expect(fee.state).toBeUndefined();
  });

  it('✕ on a child opened from a list-opened group view goes back past the group view (history not grown)', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    const router = TestBed.inject(Router);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    const location = TestBed.inject(Location);
    const go = vi.spyOn(location, 'historyGo');
    const back = vi.spyOn(location, 'back');
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(splitChild());
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(go).toHaveBeenCalledWith(-2);
    expect(back).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('✕ on a child of a deep-linked group view goes to the parent list, never to the group view', () => {
    openedWith({ viaGroup: true });
    const router = TestBed.inject(Router);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    const location = TestBed.inject(Location);
    const back = vi.spyOn(location, 'back');
    const go = vi.spyOn(location, 'historyGo');
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(splitChild());
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(navigate).toHaveBeenCalledWith('/accounting');
    expect(back).not.toHaveBeenCalled();
    expect(go).not.toHaveBeenCalled();
  });

  it('✕ on a child on the calendar goes back past the group view (history not grown), not to the day by a new entry', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    const router = TestBed.inject(Router);
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    const location = TestBed.inject(Location);
    const go = vi.spyOn(location, 'historyGo').mockImplementation(() => undefined);
    const back = vi.spyOn(location, 'back');
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    query = convertToParamMap({ month: '2026-10', day: '2026-10-22' });
    const fixture = render(splitChild());
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(go).toHaveBeenCalledExactlyOnceWith(-2);
    expect(navigate).not.toHaveBeenCalled();
    expect(back).not.toHaveBeenCalled();
  });

  it('✕ on a child of a group view opened from the reminder centre returns there', () => {
    openedWith({ closeTo: 'reminders', groupDepth: 2, viaGroup: true });
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = render(splitChild());
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(navigate).toHaveBeenCalledWith('/accounting/reminders');
  });

  it('keeps a locked split member uneditable', () => {
    const fixture = render(splitChild({ locked: true }));
    expect(fixture.componentInstance.canEdit()).toBe(false);
    expect(el(fixture).querySelector('.group-back')).not.toBeNull();
  });

  it('mounts the einvoice card and reloads on changed', () => {
    const fixture = render(detail({ id: 42, einvoice: {
      id: 7, number: 'AB-12345678', issue_date: '2026-10-03', seller_name: '範例超市', total_amount: '230',
      linked_by: 'auto', linked_to: 'entry', items: [],
    } }));
    expect(el(fixture).querySelector('app-entry-einvoice-card')).not.toBeNull();
    const unlink = [...el(fixture).querySelectorAll('app-entry-einvoice-card button')].find((b) => b.textContent?.includes('解除連結')) as HTMLButtonElement;
    unlink.click();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ id: 7, state: 'unmatched' });
    http.expectOne((r) => r.url === '/api/accounting/entries/42').flush(detail({ id: 42 }));
  });

  describe('settling across currencies', () => {
    const ACCOUNTS_X = [
      { id: 3, name: '玉山 UNI', currency: 'TWD', is_archived: false },
      { id: 21, name: 'Wise 美元', currency: 'USD', is_archived: false },
      { id: 7, name: '錢包', currency: 'TWD', is_archived: false },
      { id: 22, name: '舊美元', currency: 'USD', is_archived: true },
    ] as unknown as LedgerAccount[];
    const USD_DEBT = detail({
      id: 42, kind: 'receivable', amount: '-20.0000', currency: 'USD', name: '代墊 機票', counterparty: 'Alan',
      open_amount: '20.0000', is_settled: false, account_id: 21,
    });
    const TWD_DEBT = detail({
      id: 42, kind: 'receivable', amount: '-640.0000', currency: 'TWD', name: '代墊 晚餐', counterparty: 'Alan',
      open_amount: '640.0000', is_settled: false,
    });

    function field(fixture: ComponentFixture<EntryDetailComponent>, selector: string, value: string): void {
      const input = el(fixture).querySelector<HTMLInputElement>(selector)!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    }

    function pick(fixture: ComponentFixture<EntryDetailComponent>, id: number): void {
      const select = el(fixture).querySelector<HTMLSelectElement>('.form-account')!;
      select.value = String(id);
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
    }

    function open(body: EntryDetail, accountId?: number): ComponentFixture<EntryDetailComponent> {
      const fixture = render(body, ACCOUNTS_X);
      el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
      fixture.detectChanges();
      if (accountId !== undefined) {
        pick(fixture, accountId);
      }
      return fixture;
    }

    function submit(fixture: ComponentFixture<EntryDetailComponent>): void {
      el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();
      fixture.detectChanges();
    }

    function posted(): Record<string, unknown> {
      const req = http.expectOne('/api/accounting/entries/42/settle');
      const body = req.request.body as Record<string, unknown>;
      req.flush({});
      http.expectOne('/api/accounting/entries/42').flush({});
      return body;
    }

    function error(fixture: ComponentFixture<EntryDetailComponent>): string | undefined {
      return el(fixture).querySelector('.form-error')?.textContent?.trim();
    }

    function value(fixture: ComponentFixture<EntryDetailComponent>, selector: string): string {
      return el(fixture).querySelector<HTMLInputElement>(selector)!.value;
    }

    it('opens cross-currency when the debt account is archived and only foreign accounts remain', () => {
      const accounts = [
        { id: 21, name: 'Wise 美元', currency: 'USD', is_archived: true },
        { id: 3, name: '玉山 UNI', currency: 'TWD', is_archived: false },
      ] as unknown as LedgerAccount[];
      const fixture = render(USD_DEBT, accounts);
      el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
      fixture.detectChanges();
      expect(value(fixture, '.form-original')).toBe('20');
      expect(value(fixture, '.form-amount')).toBe('');
    });

    it('keeps a typed 金額 when switching between same-currency accounts on settle', () => {
      const fixture = open(TWD_DEBT);
      field(fixture, '.form-amount', '123');
      pick(fixture, 7);
      expect(value(fixture, '.form-amount')).toBe('123');
    });

    it('keeps a typed 金額 when switching accounts on the refund panel', () => {
      const fixture = render(detail({ id: 42, amount: '-500.0000', name: '外套' }), ACCOUNTS_X);
      el(fixture).querySelector<HTMLButtonElement>('.action-refund')!.click();
      fixture.detectChanges();
      field(fixture, '.form-amount', '123');
      pick(fixture, 7);
      expect(value(fixture, '.form-amount')).toBe('123');
    });

    it('keeps 抵銷 but clears 金額 when switching foreign to foreign', () => {
      const accounts = [
        ...ACCOUNTS_X,
        { id: 23, name: '日幣現金', currency: 'JPY', is_archived: false },
      ] as unknown as LedgerAccount[];
      const fixture = render(TWD_DEBT, accounts);
      el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
      fixture.detectChanges();
      pick(fixture, 21);
      field(fixture, '.form-amount', '20');
      field(fixture, '.form-original', '300');
      pick(fixture, 23);
      expect(value(fixture, '.form-amount')).toBe('');
      expect(value(fixture, '.form-original')).toBe('300');
    });

    it('lists the entry currency first, then the other accounts with their code', () => {
      const fixture = open(USD_DEBT);
      const options = Array.from(el(fixture).querySelectorAll('.form-account option')).map(o => o.textContent?.trim());
      expect(options).toEqual(['Wise 美元', '玉山 UNI (TWD)', '錢包 (TWD)']);
    });

    it('lists TWD accounts first for a TWD debt', () => {
      const fixture = open(TWD_DEBT);
      expect(Array.from(el(fixture).querySelectorAll('.form-account option')).map(o => o.textContent?.trim())).toEqual(['玉山 UNI', '錢包', 'Wise 美元 (USD)']);
    });

    it('keeps the refund panel on same-currency accounts, without original_amount', () => {
      const fixture = render(detail({ id: 42, amount: '-500.0000', name: '外套' }), ACCOUNTS_X);
      el(fixture).querySelector<HTMLButtonElement>('.action-refund')!.click();
      fixture.detectChanges();
      expect(Array.from(el(fixture).querySelectorAll('.form-account option')).map(o => o.textContent?.trim())).toEqual(['玉山 UNI', '錢包']);
      field(fixture, '.form-amount', '300');
      submit(fixture);
      const req = http.expectOne('/api/accounting/entries/42/refund');
      expect('original_amount' in (req.request.body as object)).toBe(false);
      req.flush({});
      http.expectOne('/api/accounting/entries/42').flush({});
    });

    it('collects a USD debt into a TWD account: 抵銷 prefilled, conversion line, original_amount sent', () => {
      const fixture = open(USD_DEBT, 3);
      expect(value(fixture, '.form-original')).toBe('20');
      expect(value(fixture, '.form-amount')).toBe('');
      expect(el(fixture).querySelector('.inline-form')!.textContent).toContain('收到金額');
      expect(el(fixture).querySelector('.inline-form')!.textContent).toContain('抵銷 USD');
      field(fixture, '.form-amount', '640');
      expect(el(fixture).querySelector('.form-conversion')?.textContent?.trim()).toBe('US$20 × 32 = $640');
      submit(fixture);
      expect(posted()).toMatchObject({ account_id: 3, amount: '640', original_amount: '20' });
    });

    it('labels the first amount 付出金額 on a payable', () => {
      const fixture = open({ ...USD_DEBT, kind: 'payable' } as EntryDetail, 3);
      expect(el(fixture).querySelector('.inline-form')!.textContent).toContain('付出金額');
    });

    it('refuses 抵銷 above the open amount and an empty 金額', () => {
      const fixture = open(USD_DEBT, 3);
      field(fixture, '.form-amount', '640');
      field(fixture, '.form-original', '21');
      submit(fixture);
      expect(error(fixture)).toBe('金額不可超過 US$20');
      field(fixture, '.form-original', '20');
      field(fixture, '.form-amount', '');
      submit(fixture);
      expect(error(fixture)).toBe('請輸入金額');
      http.expectNone('/api/accounting/entries/42/settle');
    });

    it('clears 金額 when switching to a foreign account and restores it (without 抵銷) when switching back', () => {
      const fixture = open(TWD_DEBT);
      expect(value(fixture, '.form-amount')).toBe('640');
      expect(el(fixture).querySelector('.form-original')).toBeNull();
      pick(fixture, 21);
      expect(value(fixture, '.form-amount')).toBe('');
      expect(value(fixture, '.form-original')).toBe('640');
      field(fixture, '.form-original', '300');
      pick(fixture, 7);
      expect(el(fixture).querySelector('.form-original')).toBeNull();
      expect(value(fixture, '.form-amount')).toBe('640');
      submit(fixture);
      const body = posted();
      expect(body).toMatchObject({ account_id: 7, amount: '640' });
      expect('original_amount' in body).toBe(false);
    });

    it('serializes 金額 in the account currency (USD keeps cents) for a TWD debt', () => {
      const fixture = open(TWD_DEBT, 21);
      field(fixture, '.form-amount', '20.5');
      submit(fixture);
      expect(posted()).toMatchObject({ account_id: 21, amount: '20.5', original_amount: '640' });
    });

    it('keeps the ledger precision of 抵銷', () => {
      const body = { ...USD_DEBT, amount: '-20.0001', open_amount: '20.0001' } as EntryDetail;
      const fixture = open(body, 3);
      expect(value(fixture, '.form-original')).toBe('20.0001');
      field(fixture, '.form-amount', '640');
      field(fixture, '.form-original', '20.0002');
      submit(fixture);
      expect(error(fixture)).toContain('金額不可超過');
      http.expectNone('/api/accounting/entries/42/settle');
      field(fixture, '.form-original', '20.0001');
      submit(fixture);
      expect(posted()).toMatchObject({ amount: '640', original_amount: '20.0001' });
    });

    it('sends a partial 抵銷 as typed', () => {
      const fixture = open({ ...USD_DEBT, amount: '-20.0001', open_amount: '20.0001' } as EntryDetail, 3);
      field(fixture, '.form-amount', '320');
      field(fixture, '.form-original', '10');
      submit(fixture);
      expect(posted()).toMatchObject({ amount: '320', original_amount: '10' });
    });

    it('handles a remainder below one display unit and bad 抵銷 input', () => {
      const fixture = open({ ...USD_DEBT, amount: '-0.0001', open_amount: '0.0001' } as EntryDetail, 3);
      expect(value(fixture, '.form-original')).toBe('0.0001');
      field(fixture, '.form-amount', '1');
      field(fixture, '.form-original', '');
      submit(fixture);
      expect(error(fixture)).toBe('請輸入金額');
      field(fixture, '.form-original', 'abc');
      submit(fixture);
      expect(error(fixture)).toBe('請輸入金額');
      http.expectNone('/api/accounting/entries/42/settle');
      field(fixture, '.form-original', '0.0001');
      submit(fixture);
      expect(posted()).toMatchObject({ amount: '1', original_amount: '0.0001' });
    });

    it('settles a 4-decimal debt above float precision exactly by default, and caps exactly', () => {
      const big = (open_amount: string) => ({ ...USD_DEBT, amount: `-${open_amount}`, open_amount }) as EntryDetail;
      let fixture = open(big('100000000000.0001'), 3);
      expect(value(fixture, '.form-original')).toBe('100000000000.0001');
      field(fixture, '.form-amount', '100000000000');
      submit(fixture);
      expect(posted()).toMatchObject({ original_amount: '100000000000.0001' });
      fixture.destroy();
      fixture = open(big('100000000000.0006'), 3);
      field(fixture, '.form-amount', '100000000000');
      submit(fixture);
      expect(posted()).toMatchObject({ original_amount: '100000000000.0006' });
      fixture.destroy();
      fixture = open(big('100000000000.0006'), 3);
      field(fixture, '.form-amount', '100000000000');
      field(fixture, '.form-original', '100000000000.0007');
      submit(fixture);
      expect(error(fixture)).toBe('金額不可超過 US$100000000000.0006');
      http.expectNone('/api/accounting/entries/42/settle');
    });

    it('rounds the previewed rate to the stored 10 decimals', () => {
      const body = { ...USD_DEBT, amount: '-1000', open_amount: '1000' } as EntryDetail;
      const fixture = open(body, 3);
      field(fixture, '.form-amount', '1000');
      field(fixture, '.form-original', '30');
      const line = el(fixture).querySelector('.form-conversion')?.textContent ?? '';
      expect(line).toContain('33.3333333333');
      expect(line).not.toContain('33.333333333333336');
    });

    it('shows a sub-cent remainder in the 抵銷 limit hint and error', () => {
      const fixture = open({ ...USD_DEBT, amount: '-0.0001', open_amount: '0.0001' } as EntryDetail, 3);
      expect(el(fixture).querySelector('.inline-form')!.textContent).toContain('上限 US$0.0001');
      field(fixture, '.form-amount', '1');
      field(fixture, '.form-original', '0.0002');
      submit(fixture);
      expect(error(fixture)).toBe('金額不可超過 US$0.0001');
    });

    it('keeps the 抵銷 limit hint plain for a whole remainder', () => {
      const fixture = open(USD_DEBT, 3);
      expect(el(fixture).querySelector('.inline-form')!.textContent).toContain('上限 US$20');
    });

    it('prefills a TWD remainder below one unit', () => {
      const fixture = open({ ...TWD_DEBT, amount: '-0.1', open_amount: '0.1' } as EntryDetail, 21);
      expect(value(fixture, '.form-original')).toBe('0.1');
      field(fixture, '.form-amount', '1');
      submit(fixture);
      expect(posted()).toMatchObject({ amount: '1', original_amount: '0.1' });
    });

    it('submits on ⏎ from the 抵銷 field', () => {
      const fixture = open(USD_DEBT, 3);
      field(fixture, '.form-amount', '640');
      const enter = key(el(fixture).querySelector('.form-original')!, { key: 'Enter' });
      expect(enter.defaultPrevented).toBe(true);
      expect(posted()).toMatchObject({ original_amount: '20' });
    });

    it('closes only the inline form on Esc from the 抵銷 field', () => {
      const fixture = open(USD_DEBT, 3);
      const esc = key(el(fixture).querySelector('.form-original')!, { key: 'Escape' });
      fixture.detectChanges();
      expect(esc.defaultPrevented).toBe(true);
      expect(el(fixture).querySelector('.inline-form')).toBeNull();
    });
  });
});

describe('settledValue', () => {
  it('counts the amount of a same-currency row even when it carries original_amount', () => {
    expect(settledValue({ amount: '10', currency: 'USD', original_amount: '320', original_currency: 'TWD' }, 'USD')).toBe(10);
  });
});
