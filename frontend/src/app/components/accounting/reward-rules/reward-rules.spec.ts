import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RewardRuleOverview, RewardRuleOverviewRule, RewardRuleStats } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService, MATCH_MEDIA } from '../../../services/layout-mode.service';
import { NavBadgeService } from '../../../services/nav-badge.service';
import { RewardRulesOverviewComponent, ruleStatsText } from './reward-rules';

@Component({ template: '' })
class BlankPage {}

const URL = '/api/accounting/reward-rules';

const NO_STATS: RewardRuleStats = { linked_entries: 0, linked_entries_12m: 0, rewards_count: 0, rewards_total: [], rewards_last_date: null };

function rule(id: number, name: string, overrides: Partial<RewardRuleOverviewRule> = {}): RewardRuleOverviewRule {
  return {
    id, account_id: 11, name, method: 'percent', rate: '1.5000', fixed_amount: null, window: 'statement_cycle',
    posting: 'after_window', delay_days: 0, post_month_offset: 1, post_day: 15, txn_rounding: 'keep', total_rounding: 'round',
    total_cap: null, shared_cap_id: null, is_basic: true, reward_account_id: null, reward_project_id: null,
    starts_on: null, ends_on: null, is_enabled: true, description: null, sort_order: 0, moze_id: null,
    state: 'active', stats: NO_STATS, ...overrides,
  };
}

const OVERVIEW: RewardRuleOverview = {
  accounts: [
    {
      account_id: 11, account_name: '華航卡', is_credit: true, is_archived: false,
      rules: [
        rule(2, '國內一般', {
          stats: {
            linked_entries: 12, linked_entries_12m: 5, rewards_count: 3,
            rewards_total: [{ currency: 'TWD', amount: '120.0000' }, { currency: 'JPY', amount: '300.0000' }],
            rewards_last_date: '2026-10-15',
          },
        }),
        rule(3, '海外加碼', { starts_on: '2026-01-01', ends_on: '2026-12-31' }),
      ],
    },
    { account_id: 12, account_name: '玉山卡', is_credit: true, is_archived: true, rules: [] },
  ],
  counts: { active: 2, disabled: 0, expired: 0, upcoming: 0, enabled_expired: 0 },
};

describe('ruleStatsText', () => {
  it('lists every segment, one 合計 entry per currency', () => {
    expect(ruleStatsText(OVERVIEW.accounts[0].rules[0].stats)).toBe('綁定 12 筆（近 12 月 5）· 已入帳 3 筆 · 合計 $120, ¥300 · 最近 2026/10/15');
  });

  it('omits 合計 without totals and 最近 without a last date', () => {
    expect(ruleStatsText(NO_STATS)).toBe('綁定 0 筆（近 12 月 0）· 已入帳 0 筆');
  });
});

describe('RewardRulesOverviewComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: 'accounting/reward-rules', component: RewardRulesOverviewComponent },
          { path: '**', component: BlankPage },
        ]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MATCH_MEDIA, useValue: null },
        { provide: NavBadgeService, useValue: { badges: signal({}) } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  const overviewRequest = (): TestRequest => http.expectOne(r => r.method === 'GET' && r.url === URL);
  const texts = (el: Element, selector: string) =>
    Array.from(el.querySelectorAll(selector)).map(node => node.textContent?.replace(/\s+/g, ' ').trim());

  async function open(url = '/accounting/reward-rules', overview: RewardRuleOverview = OVERVIEW) {
    TestBed.inject(LayoutModeService).set('panes');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    harness.detectChanges();
    const request = overviewRequest();
    const el = harness.routeNativeElement as HTMLElement;
    expect(el.querySelector('.rr-list')?.getAttribute('aria-busy')).toBe('true');
    request.flush(overview);
    harness.detectChanges();
    return { harness, el, request };
  }

  /** Waits for a query-param navigation to settle and returns the overview read it triggered. */
  async function nextRequest(harness: RouterTestingHarness): Promise<TestRequest> {
    let request: TestRequest | undefined;
    await vi.waitFor(() => {
      harness.detectChanges();
      const open = http.match(r => r.method === 'GET' && r.url === URL);
      expect(open.length).toBe(1);
      request = open[0];
    });
    return request!;
  }

  it('(a) reads ?state=active by default and renders one card per account, one row per rule', async () => {
    const { el, request } = await open();
    expect(request.request.urlWithParams).toBe(`${URL}?state=active`);
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('回饋規則總覽');
    expect(el.querySelector('.rr-list')?.getAttribute('aria-busy')).toBeNull();
    expect(texts(el, '.rr-card h2')).toEqual(['華航卡', '玉山卡（已封存）']);
    const card = el.querySelectorAll('.rr-card')[0];
    expect(card.querySelectorAll('.rr-rule').length).toBe(2);
    expect(texts(card, '.rr-stats')).toEqual([
      '綁定 12 筆（近 12 月 5）· 已入帳 3 筆 · 合計 $120, ¥300 · 最近 2026/10/15',
      '綁定 0 筆（近 12 月 0）· 已入帳 0 筆',
    ]);
    expect(texts(card, '.rr-label')).toEqual(['國內一般 (1.5%)', '海外加碼 (1.5%)']);
    expect(texts(card, '.rr-posting')[0]).toBe('帳單結帳後第 1 月 15 日');
    expect(texts(card, '.rr-period')).toEqual(['無期限', '2026/01/01 – 2026/12/31']);
    expect(texts(card, '.rule-state')).toEqual(['啟用中', '啟用中']);
    expect(texts(el, '.rr-filter')).toEqual(['啟用中', '已到期', '尚未開始', '已停用', '全部']);
    expect(Array.from(el.querySelectorAll('.rr-filter')).map(b => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false', 'false', 'false']);
  });

  it('passes account_id (repeatable), state and archived from the URL', async () => {
    const { request } = await open('/accounting/reward-rules?account_id=11&account_id=12&state=all&archived=1');
    expect(request.request.params.getAll('account_id')).toEqual(['11', '12']);
    expect(request.request.params.get('state')).toBe('all');
    expect(request.request.params.get('include_archived_accounts')).toBe('true');
  });

  it('(b) shows the 已到期 callout and filters to state=expired on click', async () => {
    const { harness, el } = await open('/accounting/reward-rules', { ...OVERVIEW, counts: { ...OVERVIEW.counts, enabled_expired: 31 } });
    const callout = el.querySelector<HTMLButtonElement>('button.rr-callout')!;
    expect(callout.getAttribute('role')).toBe('status');
    expect(callout.textContent?.trim()).toBe('31 條規則啟用中但已到期，記帳時不會出現');

    callout.click();
    const request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=expired`);
    request.flush({ ...OVERVIEW, counts: { ...OVERVIEW.counts, enabled_expired: 31 } });
    harness.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules?state=expired');
    expect(el.querySelector('.rr-filter[aria-pressed="true"]')?.textContent?.trim()).toBe('已到期');
  });

  it('(c) hides the callout when no enabled rule has expired', async () => {
    const { el } = await open();
    expect(el.querySelector('.rr-callout')).toBeNull();
  });

  it('a filter chip navigates with state and keeps the other params', async () => {
    const { harness, el } = await open('/accounting/reward-rules?account_id=11');
    el.querySelectorAll<HTMLButtonElement>('.rr-filter')[4].click();
    const request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?account_id=11&state=all`);
    request.flush(OVERVIEW);
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules?account_id=11&state=all');
  });

  it('(d) 顯示封存 adds include_archived_accounts=true and unchecking drops it', async () => {
    const { harness, el } = await open();
    const box = el.querySelector<HTMLInputElement>('input.rr-archived')!;
    expect(box.checked).toBe(false);
    expect(box.closest('label')?.textContent?.trim()).toBe('顯示封存');

    box.click();
    let request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=active&include_archived_accounts=true`);
    request.flush(OVERVIEW);
    harness.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules?archived=1');
    expect(box.checked).toBe(true);

    box.click();
    request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=active`);
    request.flush(OVERVIEW);
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules');
  });

  it('(e) shows 尚無回饋規則 without accounts and 尚無規則 → 新增 on an empty card', async () => {
    const { el } = await open();
    const empty = el.querySelectorAll('.rr-card')[1];
    expect(empty.querySelector('.rr-empty')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('尚無規則 → 新增');
    expect(empty.querySelector('.rr-empty a')?.getAttribute('href')).toBe('/accounting/accounts/12/settings');
    expect(el.textContent).not.toContain('尚無回饋規則');
  });

  it('(e) shows 尚無回饋規則 when no account has rules', async () => {
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [] });
    expect(el.querySelector('.rr-card')).toBeNull();
    expect(el.querySelector('.rr-none')?.textContent?.trim()).toBe('尚無回饋規則');
  });

  it('(f) links each rule to its account settings with ?rule=<id>', async () => {
    const { el } = await open();
    expect(el.querySelector('a.rr-rule')?.getAttribute('href')).toBe('/accounting/accounts/11/settings?rule=2');
  });

  it('shows a load failure in role=alert', async () => {
    TestBed.inject(LayoutModeService).set('panes');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/accounting/reward-rules');
    harness.detectChanges();
    overviewRequest().flush({ detail: '讀取失敗了' }, { status: 500, statusText: 'Server Error' });
    harness.detectChanges();
    const el = harness.routeNativeElement as HTMLElement;
    expect(el.querySelector('[role="alert"]')?.textContent?.trim()).toBe('讀取失敗了');
    expect(el.querySelector('.rr-list')?.getAttribute('aria-busy')).toBeNull();
  });

  it('reloads after a reward rule write elsewhere (accountsChanged)', async () => {
    const { harness } = await open();
    TestBed.inject(AccountingService).deleteRewardRule(11, 3).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/accounts/11/reward-rules/3').flush(null);
    harness.detectChanges();
    const request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=active`);
    request.flush(OVERVIEW);
  });

  it('gives phones a back button to 記帳設定 before the h1', async () => {
    TestBed.inject(LayoutModeService).set('phone');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/accounting/reward-rules');
    harness.detectChanges();
    overviewRequest().flush(OVERVIEW);
    harness.detectChanges();
    const el = harness.routeNativeElement as HTMLElement;
    const back = el.querySelector<HTMLButtonElement>('button.rr-back')!;
    expect(back.getAttribute('aria-label')).toBe('返回');
    back.click();
    await vi.waitFor(() => expect(TestBed.inject(Router).url).toBe('/accounting/settings'));
  });
});
