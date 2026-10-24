import { Component, Input } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { ChartModule } from 'primeng/chart';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { portfolioAccountsMatcher } from '../../../app.routes';
import { BrokerAccount } from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PaneStateService } from '../../../services/pane-state.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioAccountDetailComponent } from './account-detail';
import { PortfolioAccountsListComponent } from './accounts-list';
import { PortfolioAccountsPageComponent } from './accounts-page';

@Component({ selector: 'p-chart', template: '' })
class ChartStubComponent {
  @Input() type = '';
  @Input() data: unknown;
  @Input() options: unknown;
}

function account(id: number, patch: Partial<BrokerAccount> = {}): BrokerAccount {
  return {
    id,
    broker: 'cathay',
    nickname: `帳戶 ${id}`,
    currency: 'TWD',
    opening_balance: '1000',
    opening_date: '2026-01-02',
    is_active: true,
    created_at: '2026-01-02T00:00:00Z',
    native_balance: '5000',
    target_balance: '5000',
    target_currency: 'TWD',
    ...patch,
  };
}

/** Inactive 3 first, active 7 second: the auto-selection must skip 3. */
const ACCOUNTS: BrokerAccount[] = [
  account(3, { nickname: '舊交割戶', is_active: false }),
  account(7, { nickname: '元大交割戶' }),
  account(9, { nickname: 'Firstrade USD', broker: 'firstrade', currency: 'USD', native_balance: '1500' }),
];

describe('PortfolioAccountsPageComponent', () => {
  let httpMock: HttpTestingController;
  let layout: LayoutModeService;
  let router: Router;

  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([{ matcher: portfolioAccountsMatcher, component: PortfolioAccountsPageComponent }]),
      ],
    }).overrideComponent(PortfolioAccountDetailComponent, {
      remove: { imports: [ChartModule] },
      add: { imports: [ChartStubComponent] },
    });
    httpMock = TestBed.inject(HttpTestingController);
    layout = TestBed.inject(LayoutModeService);
    router = TestBed.inject(Router);
  });

  afterEach(() => {
    httpMock.verify();
    layout.set(null);
    localStorage.clear();
    vi.restoreAllMocks();
  });

  async function open(url: string) {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    return harness;
  }

  async function settle(harness: RouterTestingHarness) {
    harness.detectChanges();
    await harness.fixture.whenStable();
    harness.detectChanges();
  }

  function listRequests(): TestRequest[] {
    return httpMock.match(req => req.url === '/api/portfolio/accounts/' && req.params.get('in_currency') === 'TWD');
  }

  function flushList(items: BrokerAccount[] = ACCOUNTS) {
    const reqs = listRequests();
    expect(reqs.length).toBe(1);
    reqs[0].flush({ items, target_currency: 'TWD', total_target_balance: '15000', skipped_currencies: [] });
  }

  /** The detail's three loads (account summary, balance history, cash transactions) for `id`; how many sets were sent. */
  function flushDetail(id: number): number {
    const summaries = httpMock.match(req => req.url === '/api/portfolio/accounts/' && req.params.get('include_inactive') === 'true');
    const histories = httpMock.match(req => req.url === `/api/portfolio/accounts/${id}/balance-history`);
    const txns = httpMock.match(req => req.url === `/api/portfolio/accounts/${id}/cash-transactions`);
    expect(summaries.length).toBe(histories.length);
    expect(txns.length).toBe(histories.length);
    summaries.forEach(req => req.flush({ items: ACCOUNTS, target_currency: null, total_target_balance: null, skipped_currencies: [] }));
    histories.forEach(req => req.flush({ account_id: id, currency: 'TWD', points: [] }));
    txns.forEach(req => req.flush({ items: [], total: 0, offset: 0, limit: 25 }));
    return histories.length;
  }

  /** Flushes every pending detail load, whichever account it is for (a switch may have cancelled some). */
  function flushAnyDetail() {
    const open = (match: (url: string) => boolean) => httpMock.match(req => match(req.url)).filter(req => !req.cancelled);
    open(url => url === '/api/portfolio/accounts/').forEach(req =>
      req.flush({ items: ACCOUNTS, target_currency: null, total_target_balance: null, skipped_currencies: [] }));
    open(url => url.endsWith('/balance-history')).forEach(req => req.flush({ account_id: 0, currency: 'TWD', points: [] }));
    open(url => url.endsWith('/cash-transactions')).forEach(req => req.flush({ items: [], total: 0, offset: 0, limit: 25 }));
  }

  function el(harness: RouterTestingHarness): HTMLElement {
    return harness.routeNativeElement as HTMLElement;
  }

  function cards(harness: RouterTestingHarness): HTMLButtonElement[] {
    return Array.from(el(harness).querySelectorAll<HTMLButtonElement>('button.acct-card'));
  }

  function headerButton(harness: RouterTestingHarness, label: string): HTMLButtonElement {
    const btn = Array.from(el(harness).querySelectorAll('app-page-header app-btn')).find(b => b.textContent?.includes(label));
    return btn!.querySelector('button')!;
  }

  function listInstance(harness: RouterTestingHarness): PortfolioAccountsListComponent {
    return harness.fixture.debugElement.query(d => d.componentInstance instanceof PortfolioAccountsListComponent).componentInstance;
  }

  describe('panes', () => {
    beforeEach(() => layout.set('panes'));

    it('shows one header, the master list and the first active account, putting it in the URL once', async () => {
      const harness = await open('/portfolio/accounts');
      const navigate = vi.spyOn(router, 'navigate');

      flushList();
      await settle(harness);

      expect(router.url).toBe('/portfolio/accounts/7');
      expect(navigate).toHaveBeenCalledTimes(1);
      expect(navigate).toHaveBeenCalledWith(['/portfolio/accounts', 7], { replaceUrl: true });

      const headers = el(harness).querySelectorAll('app-page-header');
      expect(headers.length).toBe(1);
      expect(headers[0].querySelector('h1')?.textContent).toContain('現金帳戶');
      expect(headers[0].textContent).toContain('券商交割戶與複委託帳戶的現金');
      expect(headerButton(harness, '記錄入出金').classList).toContain('btn-soft');
      expect(headerButton(harness, '新增帳戶').classList).toContain('btn-soft');

      expect(el(harness).querySelector('.accounts-md-master app-portfolio-accounts-list .acct-master')).not.toBeNull();
      expect(cards(harness).length).toBe(3);
      expect(el(harness).querySelector('.accounts-md-detail app-portfolio-account-detail')).not.toBeNull();
      // One set of detail loads, for 7 — the list is not refetched by the URL change.
      expect(flushDetail(7)).toBe(1);
      expect(listRequests().length).toBe(0);
      await settle(harness);
      expect(cards(harness)[1].getAttribute('aria-pressed')).toBe('true');
      expect(el(harness).querySelector('.accounts-md-detail h2')?.textContent).toContain('元大交割戶');

      // The same accounts loaded again: no second navigation.
      listInstance(harness).loaded.emit(ACCOUNTS);
      await settle(harness);
      expect(navigate).toHaveBeenCalledTimes(1);
      expect(flushDetail(7)).toBe(0);
    });

    it('navigates once while the URL has not caught up, however often the list loads', async () => {
      const harness = await open('/portfolio/accounts');
      const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);

      flushList();
      await settle(harness);
      listInstance(harness).loaded.emit(ACCOUNTS);
      await settle(harness);
      // A reload whose first active account differs, and a mode round trip, before the router lands.
      listInstance(harness).fetchAccounts();
      flushList([ACCOUNTS[2], ACCOUNTS[1]]);
      await settle(harness);
      layout.set('sheet');
      await settle(harness);
      listRequests().forEach(req => req.flush({ items: ACCOUNTS, target_currency: 'TWD', total_target_balance: '0', skipped_currencies: [] }));
      layout.set('panes');
      await settle(harness);

      expect(navigate).toHaveBeenCalledTimes(1);
      expect(router.url).toBe('/portfolio/accounts');
      flushAnyDetail();
    });

    it('a cash-ledger change refetches the list and the embedded detail without navigating again', async () => {
      const harness = await open('/portfolio/accounts');
      const navigate = vi.spyOn(router, 'navigate');
      flushList();
      await settle(harness);
      expect(flushDetail(7)).toBe(1);
      await settle(harness);
      expect(navigate).toHaveBeenCalledTimes(1);
      const loaded: BrokerAccount[][] = [];
      listInstance(harness).loaded.subscribe(items => loaded.push(items));

      TestBed.inject(PortfolioService).notifyCashLedgerChanged();
      flushList();
      expect(flushDetail(7)).toBe(1);
      await settle(harness);

      expect(loaded).toEqual([ACCOUNTS]);
      expect(navigate).toHaveBeenCalledTimes(1);
      expect(router.url).toBe('/portfolio/accounts/7');
      expect(flushDetail(7)).toBe(0);
    });

    it('does not navigate or request a detail for an empty list', async () => {
      const harness = await open('/portfolio/accounts');
      const navigate = vi.spyOn(router, 'navigate');

      flushList([]);
      await settle(harness);

      expect(TestBed.inject(PaneStateService).paneOpen()).toBe(false);
      expect(navigate).not.toHaveBeenCalled();
      expect(router.url).toBe('/portfolio/accounts');
      expect(el(harness).querySelector('app-portfolio-account-detail')).toBeNull();
      expect(httpMock.match(req => req.url.includes('/balance-history')).length).toBe(0);
    });

    it('selects the route account and reloads the detail once when another card is clicked', async () => {
      const harness = await open('/portfolio/accounts/9');
      const navigate = vi.spyOn(router, 'navigate');
      flushList();
      await settle(harness);
      expect(flushDetail(9)).toBe(1);
      await settle(harness);

      expect(navigate).not.toHaveBeenCalled();
      const card9 = cards(harness)[2];
      expect(card9.getAttribute('aria-pressed')).toBe('true');
      expect(card9.classList).toContain('selected');
      expect(cards(harness)[1].getAttribute('aria-pressed')).toBe('false');

      cards(harness)[1].click();
      await settle(harness);

      expect(router.url).toBe('/portfolio/accounts/7');
      expect(flushDetail(7)).toBe(1);
      expect(flushDetail(9)).toBe(0);
      expect(listRequests().length).toBe(0);
      await settle(harness);
      expect(cards(harness)[1].classList).toContain('selected');
    });

    it('opens the list cash form from 記錄入出金 and the create dialog from 新增帳戶', async () => {
      const harness = await open('/portfolio/accounts/7');
      flushList();
      await settle(harness);
      flushDetail(7);
      const list = listInstance(harness);

      headerButton(harness, '記錄入出金').click();
      await settle(harness);
      expect(list.cashFormVisible()).toBe(true);
      expect(document.querySelector('form.cash-flow-form')).not.toBeNull();

      headerButton(harness, '新增帳戶').click();
      await settle(harness);
      expect(list.createDialogVisible()).toBe(true);
      expect(document.querySelector('form.account-form')).not.toBeNull();
    });

    it('never writes the shell pane state (its detail is always visible)', async () => {
      const paneState = TestBed.inject(PaneStateService);
      const harness = await open('/portfolio/accounts/7');
      flushList();
      await settle(harness);
      flushDetail(7);
      expect(paneState.paneOpen()).toBe(false);
      expect(paneState.fabRight()).toBeNull();
    });
  });

  for (const mode of ['sheet', 'phone'] as const) {
    describe(mode, () => {
      beforeEach(() => layout.set(mode));

      it('shows the compact page (header, total, cards) at /portfolio/accounts without navigating or a pane', async () => {
        const harness = await open('/portfolio/accounts');
        const navigate = vi.spyOn(router, 'navigate');
        flushList();
        await settle(harness);

        expect(navigate).not.toHaveBeenCalled();
        expect(router.url).toBe('/portfolio/accounts');
        expect(TestBed.inject(PaneStateService).paneOpen()).toBe(false);
        expect(el(harness).querySelector('app-portfolio-account-detail')).toBeNull();
        expect(el(harness).querySelector('.accounts-md')).toBeNull();

        const headers = el(harness).querySelectorAll('app-page-header');
        expect(headers.length).toBe(1);
        expect(headers[0].parentElement?.classList).toContain('accounts-compact');
        expect(headers[0].querySelector('h1')?.textContent).toContain('現金帳戶');
        expect(headers[0].textContent).toContain('券商交割戶與複委託帳戶的現金');
        expect(headers[0].querySelectorAll('app-btn').length).toBe(2);
        expect(headerButton(harness, '記錄入出金').classList).toContain('btn-soft');
        expect(headerButton(harness, '新增帳戶').classList).toContain('btn-soft');
        // Phones put the actions on their own line so the title is not squeezed at 390 px.
        expect(headers[0].classList.contains('actions-below')).toBe(mode === 'phone');

        // The width hook (560 px cap on sheet, full width on phone): the list sits directly in `.accounts-compact`.
        expect(el(harness).querySelector('.accounts-compact > app-portfolio-accounts-list .acct-master')).not.toBeNull();
        expect(el(harness).querySelector('.acct-master .acct-total')?.textContent).toContain('現金帳戶總額 · TWD');
        expect(cards(harness).length).toBe(3);
        expect(cards(harness).some(card => card.getAttribute('aria-pressed') === 'true')).toBe(false);
        expect(cards(harness).some(card => card.classList.contains('selected'))).toBe(false);
      });

      it('opens the account detail when a card is tapped', async () => {
        const harness = await open('/portfolio/accounts');
        flushList();
        await settle(harness);

        cards(harness)[0].click();
        await settle(harness);

        expect(router.url).toBe('/portfolio/accounts/3');
        expect(el(harness).querySelector('app-portfolio-accounts-list')).toBeNull();
        expect(el(harness).querySelector('app-portfolio-account-detail app-page-header')).not.toBeNull();
        expect(flushDetail(3)).toBe(1);
        expect(TestBed.inject(PaneStateService).paneOpen()).toBe(false);
      });

      it('shows only the route-driven detail at /portfolio/accounts/:id', async () => {
        const harness = await open('/portfolio/accounts/9');
        await settle(harness);

        expect(listRequests().length).toBe(0);
        expect(el(harness).querySelector('app-portfolio-accounts-list')).toBeNull();
        expect(el(harness).querySelector('app-portfolio-account-detail app-page-header')).not.toBeNull();
        expect(flushDetail(9)).toBe(1);
      });

      it('opens the list cash form from 記錄入出金 and the create dialog from 新增帳戶', async () => {
        const harness = await open('/portfolio/accounts');
        flushList();
        await settle(harness);
        const list = listInstance(harness);
        const cashForm = vi.spyOn(list, 'openCashForm');
        const createDialog = vi.spyOn(list, 'openCreateDialog');

        headerButton(harness, '記錄入出金').click();
        expect(cashForm).toHaveBeenCalledTimes(1);
        expect(createDialog).not.toHaveBeenCalled();

        headerButton(harness, '新增帳戶').click();
        expect(createDialog).toHaveBeenCalledTimes(1);
      });

      it('auto-selects the first active account once on growing to panes', async () => {
        const harness = await open('/portfolio/accounts');
        const navigate = vi.spyOn(router, 'navigate');
        flushList();
        await settle(harness);
        expect(navigate).not.toHaveBeenCalled();

        layout.set('panes');
        await settle(harness);
        flushList(); // the panes master column's own list
        await settle(harness);

        expect(navigate).toHaveBeenCalledTimes(1);
        expect(navigate).toHaveBeenCalledWith(['/portfolio/accounts', 7], { replaceUrl: true });
        expect(router.url).toBe('/portfolio/accounts/7');
        flushAnyDetail();
      });

      it('keeps the detail on shrinking from panes at /portfolio/accounts/7 and selects 7 on growing back', async () => {
        layout.set('panes');
        const harness = await open('/portfolio/accounts/7');
        const navigate = vi.spyOn(router, 'navigate');
        flushList();
        await settle(harness);
        expect(flushDetail(7)).toBe(1);

        layout.set(mode);
        await settle(harness);
        expect(router.url).toBe('/portfolio/accounts/7');
        expect(el(harness).querySelector('app-portfolio-accounts-list')).toBeNull();
        expect(el(harness).querySelector('.accounts-compact')).toBeNull();
        expect(el(harness).querySelector('app-portfolio-account-detail app-page-header')).not.toBeNull();
        expect(TestBed.inject(PaneStateService).paneOpen()).toBe(false);
        expect(flushDetail(7)).toBe(1); // the route-driven detail of the compact layout

        layout.set('panes');
        await settle(harness);
        flushList();
        await settle(harness);
        expect(flushDetail(7)).toBe(1);
        await settle(harness);

        expect(navigate).not.toHaveBeenCalled();
        expect(el(harness).querySelector('.accounts-md-detail app-portfolio-account-detail')).not.toBeNull();
        expect(cards(harness)[1].getAttribute('aria-pressed')).toBe('true');
        expect(TestBed.inject(PaneStateService).paneOpen()).toBe(false);
      });
    });
  }
});
