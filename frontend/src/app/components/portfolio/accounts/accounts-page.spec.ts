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

    it('does not navigate or request a detail for an empty list', async () => {
      const harness = await open('/portfolio/accounts');
      const navigate = vi.spyOn(router, 'navigate');

      flushList([]);
      await settle(harness);

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

    it('holds PaneStateService.paneOpen while shown and clears it on destroy', async () => {
      const paneState = TestBed.inject(PaneStateService);
      const harness = await open('/portfolio/accounts/7');
      flushList();
      await settle(harness);
      flushDetail(7);
      expect(paneState.paneOpen()).toBe(true);
      expect(paneState.formOpen()).toBe(false);
      expect(paneState.sheetOpen()).toBe(false);

      layout.set('sheet');
      await settle(harness);
      expect(paneState.paneOpen()).toBe(false);
      flushDetail(7); // the route-driven detail of the sheet layout

      layout.set('panes');
      await settle(harness);
      expect(paneState.paneOpen()).toBe(true);
      flushList();
      await settle(harness);
      flushDetail(7);

      harness.fixture.destroy();
      expect(paneState.paneOpen()).toBe(false);
    });
  });

  for (const mode of ['sheet', 'phone'] as const) {
    describe(mode, () => {
      beforeEach(() => layout.set(mode));

      it('shows only the list (with its own header) at /portfolio/accounts', async () => {
        const harness = await open('/portfolio/accounts');
        const navigate = vi.spyOn(router, 'navigate');
        flushList();
        await settle(harness);

        expect(navigate).not.toHaveBeenCalled();
        expect(router.url).toBe('/portfolio/accounts');
        expect(el(harness).querySelector('app-portfolio-account-detail')).toBeNull();
        expect(el(harness).querySelector('.acct-master')).toBeNull();
        expect(el(harness).querySelectorAll('app-page-header').length).toBe(1);
        expect(el(harness).querySelector('app-portfolio-accounts-list .accounts-page app-page-header')).not.toBeNull();
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
    });
  }
});
