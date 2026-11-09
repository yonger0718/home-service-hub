import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  RewardRuleOverview, RewardRuleOverviewAccount, RewardRuleOverviewRule, RewardRuleStats, RewardUsage,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService, MATCH_MEDIA } from '../../../services/layout-mode.service';
import { NavBadgeService } from '../../../services/nav-badge.service';
import { RewardRulesOverviewComponent } from './reward-rules';

@Component({ template: '' })
class BlankPage {}

const URL = '/api/accounting/reward-rules';

const NO_STATS: RewardRuleStats = {
  linked_entries: 0, linked_entries_12m: 0, rewards_count: 0, rewards_total: [], rewards_last_date: null,
};

function rule(id: number, account_id: number, name: string, over: Partial<RewardRuleOverviewRule> = {}): RewardRuleOverviewRule {
  return {
    id, account_id, name, method: 'percent', rate: '1.0000', fixed_amount: null, unit_spend: null,
    window: 'statement_cycle', posting: 'after_window', delay_days: 0, post_month_offset: 1, post_day: 15,
    txn_rounding: 'keep', total_rounding: 'round', total_cap: null, shared_cap_id: null, is_basic: false,
    reward_account_id: null, reward_project_id: null, starts_on: null, ends_on: null, is_enabled: true,
    description: null, sort_order: 0, moze_id: null, state: 'active', stats: NO_STATS, ...over,
  };
}

function usage(over: Partial<RewardUsage> = {}): RewardUsage {
  return {
    rule_ids: [2], shared_cap_id: null, name: '一般消費', window_start: '2026-10-26', window_end: '2026-11-25',
    used: '236.0000', estimated: '64.0000', cap: '500.0000', remaining: '200.0000',
    reward_account_id: 11, reward_account_name: '測試卡 A', reward_is_money: true, ...over,
  };
}

function account(account_id: number, account_name: string, over: Partial<RewardRuleOverviewAccount> = {}): RewardRuleOverviewAccount {
  return {
    account_id, account_name, is_credit: true, is_archived: false, icon: null, logo: null, logo_hash: null,
    closing_day: null, usage: [], rules: [], ...over,
  };
}

const MILES = { reward_account_id: 90, reward_account_name: '測試里程', reward_is_money: false };

/** Three cards with rules, one credit card without, one bank account without; 7 rules in 4 states. */
const OVERVIEW: RewardRuleOverview = {
  accounts: [
    account(11, '測試卡 A', {
      closing_day: 25, logo: 'test-bank',
      usage: [
        usage(),
        usage({ rule_ids: [3], name: '全聯・家樂福加碼', used: '180.0000', estimated: '13.0000', cap: '200.0000', remaining: '7.0000' }),
      ],
      rules: [
        rule(2, 11, '一般消費', { stats: { ...NO_STATS, linked_entries_12m: 200, rewards_count: 9 } }),
        rule(3, 11, '全聯・家樂福加碼', { rate: '5.0000', stats: { ...NO_STATS, linked_entries_12m: 14 } }),
      ],
    }),
    account(12, '測試卡 B', {
      usage: [usage({ rule_ids: [4], name: '玩數位', window_start: '2026-11-01', window_end: '2026-11-30',
        used: '0.0000', estimated: '28.0000', cap: null, remaining: null, reward_account_id: 12,
        reward_account_name: '測試卡 B' })],
      rules: [
        rule(4, 12, '玩數位 3%', { rate: '3.0000', window: 'calendar_month' }),
        rule(5, 12, '基本回饋', { rate: '0.3000', is_basic: true, is_enabled: false, state: 'disabled' }),
      ],
    }),
    account(13, '測試卡 C', {
      closing_day: 5,
      usage: [usage({ rule_ids: [6], name: '哩程', window_start: '2026-11-06', window_end: '2026-12-05',
        used: '300.0000', estimated: '0.0000', remaining: '200.0000', ...MILES })],
      rules: [
        rule(6, 13, '哩程', { reward_account_id: 90 }),
        rule(7, 13, '明年加碼', { starts_on: '2027-01-01', state: 'upcoming' }),
        rule(8, 13, '舊活動', { ends_on: '2026-10-31', state: 'expired' }),
      ],
    }),
    account(14, '沒規則的卡'),
    account(15, '銀行戶', { is_credit: false }),
  ],
  counts: { active: 4, disabled: 1, expired: 1, upcoming: 1, enabled_expired: 1 },
};

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
  const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim();
  const texts = (el: Element, selector: string) => Array.from(el.querySelectorAll(selector)).map(text);

  async function open(url = '/accounting/reward-rules', overview: RewardRuleOverview = OVERVIEW) {
    TestBed.inject(LayoutModeService).set('panes');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    harness.detectChanges();
    const request = overviewRequest();
    const el = harness.routeNativeElement as HTMLElement;
    expect(el.querySelector('.rr-grid')?.getAttribute('aria-busy')).toBe('true');
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

  const card = (el: Element, name: string) =>
    Array.from(el.querySelectorAll<HTMLElement>('.rr-card')).find(c => text(c.querySelector('.rr-card-name')) === name)!;

  it('requests GET /reward-rules once with state=all by default and shows the chips with counts', async () => {
    const { el, request } = await open();
    expect(request.request.urlWithParams).toBe(`${URL}?state=all`);
    expect(text(el.querySelector('h1'))).toBe('回饋規則');
    expect(el.querySelector('.rr-grid')?.getAttribute('aria-busy')).toBeNull();
    expect(texts(el, '.rr-filter')).toEqual(['全部 7', '生效中 4', '已停用 1', '已過期 1', '未開始 1']);
    expect(Array.from(el.querySelectorAll('.rr-filter')).map(b => b.getAttribute('aria-pressed')))
      .toEqual(['true', 'false', 'false', 'false', 'false']);
    expect(texts(el, '.rr-card-name')).toEqual(['測試卡 A', '測試卡 B', '測試卡 C']);
  });

  it('headline: cards, rules, the money total with estimates, then the folded points units', async () => {
    const { el } = await open();
    expect(text(el.querySelector('.rr-headline'))).toBe('3 張卡 · 7 條規則 · 本期回饋合計 $521（含預估） ＋ 1 種點數');
  });

  it('headline without points units has no ＋ part', async () => {
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: OVERVIEW.accounts.slice(0, 2) });
    expect(text(el.querySelector('.rr-headline'))).toBe('2 張卡 · 7 條規則 · 本期回饋合計 $521（含預估）');
  });

  it('card header: icon, window line from the closing day and the usage rows, the period total', async () => {
    const { el } = await open();
    const a = card(el, '測試卡 A');
    expect(a.querySelector('app-account-icon')).not.toBeNull();
    expect(text(a.querySelector('.rr-window'))).toBe('結帳日 25 號 · 本期 10/26–11/25');
    expect(texts(a, '.rr-total')).toEqual(['$493']);
    expect(text(a.querySelector('.rr-total-label'))).toBe('本期回饋（含預估）');
  });

  it('calendar card: 每月 1 日起算 · 本月', async () => {
    const { el } = await open();
    expect(text(card(el, '測試卡 B').querySelector('.rr-window'))).toBe('每月 1 日起算 · 本月 11/1–11/30');
  });

  it('mixed windows: header keeps only the closing day and each row carries its own 本期', async () => {
    const mixed = account(21, '混合卡', {
      closing_day: 25,
      usage: [
        usage({ rule_ids: [31], reward_account_id: 21 }),
        usage({ rule_ids: [32], window_start: '2026-11-01', window_end: '2026-11-30', reward_account_id: 21 }),
      ],
      rules: [rule(31, 21, '帳單週期'), rule(32, 21, '自然月', { window: 'calendar_month' })],
    });
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [mixed] });
    const c = card(el, '混合卡');
    expect(text(c.querySelector('.rr-window'))).toBe('結帳日 25 號');
    expect(texts(c, '.rr-row-window')).toEqual(['本期 10/26–11/25', '本期 11/1–11/30']);
  });

  it('same start, different end still counts as mixed', async () => {
    const mixed = account(22, '三月卡', {
      closing_day: 30,
      usage: [
        usage({ rule_ids: [41], window_start: '2026-03-01', window_end: '2026-03-30', reward_account_id: 22 }),
        usage({ rule_ids: [42], window_start: '2026-03-01', window_end: '2026-03-31', reward_account_id: 22 }),
      ],
      rules: [rule(41, 22, '帳單週期'), rule(42, 22, '自然月', { window: 'calendar_month' })],
    });
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [mixed] });
    const c = card(el, '三月卡');
    expect(text(c.querySelector('.rr-window'))).toBe('結帳日 30 號');
    expect(texts(c, '.rr-row-window')).toEqual(['本期 3/1–3/30', '本期 3/1–3/31']);
  });

  it('calendar rows hidden by the filter: a whole-month window still reads as the calendar line', async () => {
    const hidden = account(23, '過濾卡', {
      usage: [usage({ rule_ids: [51], window_start: '2026-11-01', window_end: '2026-11-30', reward_account_id: 23 })],
      rules: [rule(52, 23, '舊活動', { ends_on: '2026-10-31', state: 'expired' })],
    });
    const { el } = await open('/accounting/reward-rules?state=expired', { ...OVERVIEW, accounts: [hidden] });
    expect(text(card(el, '過濾卡').querySelector('.rr-window'))).toBe('每月 1 日起算 · 本月 11/1–11/30');
  });

  it('rule row: title without the repeated rate, the rate chip, posting, and the near-cap bar', async () => {
    const { el } = await open();
    const rows = card(el, '測試卡 A').querySelectorAll('.rr-rule');
    expect(texts(rows[1], '.rr-title')).toEqual(['全聯・家樂福加碼']);
    expect(texts(rows[1], '.rr-rate')).toEqual(['5%']);
    expect(text(rows[1].querySelector('.rr-posting'))).toBe('帳單結帳後第 1 月 15 日');
    expect(text(rows[1].querySelector('.cap-text'))).toBe('$193 / $200 · 快到上限');
    expect(rows[1].querySelector('.cap-bar.near')).not.toBeNull();
    expect(rows[1].querySelector('.rr-state')).toBeNull();
    expect(rows[1].getAttribute('href')).toBe('/accounting/accounts/11/settings?rule=3');
    expect(text(rows[0].querySelector('.cap-text'))).toBe('$300 / $500 · 剩 $200');
    const b = card(el, '測試卡 B').querySelectorAll('.rr-rule');
    expect([text(b[0].querySelector('.rr-title')), text(b[0].querySelector('.rr-rate'))]).toEqual(['玩數位', '3%']);
    expect(text(b[0].querySelector('.cap-text'))).toBe('無上限 · 本期 $0 已入帳 + $28 預估');
  });

  it('state tags: the state on a non-active rule, none on a plain active one', async () => {
    const { el } = await open();
    const b = card(el, '測試卡 B').querySelectorAll('.rr-rule');
    expect(text(b[1].querySelector('.rr-state'))).toBe('已停用');
    expect(b[1].querySelector('.rr-state')?.getAttribute('data-state')).toBe('disabled');
    expect(card(el, '測試卡 A').querySelector('.rr-state')).toBeNull();
  });

  it('state tags: 基本 on an active basic rule', async () => {
    const basic = account(24, '基本卡', { rules: [rule(61, 24, '基本回饋', { is_basic: true })] });
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [basic] });
    expect(text(el.querySelector('.rr-rule .rr-state'))).toBe('基本');
    expect(el.querySelector('.rr-rule .rr-state')?.getAttribute('data-state')).toBe('basic');
  });

  it('inactive rows: dimmed, the muted line instead of a bar', async () => {
    const { el } = await open();
    const b = card(el, '測試卡 B').querySelectorAll('.rr-rule');
    expect(b[1].classList.contains('inactive')).toBe(true);
    expect(text(b[1].querySelector('.rr-muted'))).toBe('停用中，不估算回饋');
    expect(b[1].querySelector('app-cap-bar')).toBeNull();
    const c = card(el, '測試卡 C').querySelectorAll('.rr-rule');
    expect(text(c[1].querySelector('.rr-muted'))).toBe('2027/1/1 起才估算');
    expect(c[1].classList.contains('inactive')).toBe(true);
    expect(c[1].querySelector('app-cap-bar')).toBeNull();
    expect(text(c[2].querySelector('.rr-muted'))).toBe('已結束，不再估算');
    expect(text(c[2].querySelector('.rr-state'))).toBe('已過期');
  });

  it('shared cap: both member rows show the same bar; a disabled member shows no bar', async () => {
    const shared = account(25, '共用卡', {
      closing_day: 25,
      usage: [usage({ rule_ids: [71, 72], shared_cap_id: 'g1', name: '共用', reward_account_id: 25 })],
      rules: [
        rule(71, 25, '甲', { shared_cap_id: 'g1' }), rule(72, 25, '乙', { shared_cap_id: 'g1' }),
        rule(73, 25, '丙', { shared_cap_id: 'g1', is_enabled: false, state: 'disabled' }),
      ],
    });
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [shared] });
    const rows = card(el, '共用卡').querySelectorAll('.rr-rule');
    expect(texts(rows[0], '.cap-text')).toEqual(['$300 / $500 · 剩 $200']);
    expect(texts(rows[1], '.cap-text')).toEqual(['$300 / $500 · 剩 $200']);
    expect(rows[2].querySelector('app-cap-bar')).toBeNull();
    expect(text(rows[2].querySelector('.rr-muted'))).toBe('停用中，不估算回饋');
    expect(texts(el, '.rr-total')).toEqual(['$300']);
  });

  it('points unit survives an ?account_id= filter: bar and card total in the points unit', async () => {
    const { el, request } = await open('/accounting/reward-rules?account_id=13', {
      ...OVERVIEW, accounts: [OVERVIEW.accounts[2]],
    });
    expect(request.request.params.getAll('account_id')).toEqual(['13']);
    const c = card(el, '測試卡 C');
    expect(text(c.querySelector('.cap-text'))).toBe('300 / 500 測試里程 · 剩 200');
    expect(texts(c, '.rr-total')).toEqual(['300 測試里程']);
  });

  it('mixed units on one card: one total per unit; the headline folds the points into ＋ N 種點數', async () => {
    const both = account(26, '雙單位卡', {
      closing_day: 25,
      usage: [
        usage({ rule_ids: [81], used: '0.0000', estimated: '64.0000', reward_account_id: 26 }),
        usage({ rule_ids: [82], used: '300.0000', estimated: '0.0000', ...MILES }),
        usage({ rule_ids: [83], used: '10.0000', estimated: '0.0000', cap: null, remaining: null,
          reward_account_id: 91, reward_account_name: '測試點數', reward_is_money: false }),
      ],
      rules: [rule(81, 26, '金額'), rule(82, 26, '哩程'), rule(83, 26, '點數')],
    });
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [both] });
    expect(texts(card(el, '雙單位卡'), '.rr-total')).toEqual(['$64', '300 測試里程', '10 測試點數']);
    expect(text(el.querySelector('.rr-headline'))).toBe('1 張卡 · 7 條規則 · 本期回饋合計 $64（含預估） ＋ 2 種點數');
  });

  it('cards without usage rows show $0 and only the closing day', async () => {
    const quiet = account(27, '安靜卡', { closing_day: 10, rules: [rule(91, 27, '舊活動', { ends_on: '2026-01-31', state: 'expired' })] });
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [quiet] });
    const c = card(el, '安靜卡');
    expect(texts(c, '.rr-total')).toEqual(['$0']);
    expect(text(c.querySelector('.rr-window'))).toBe('結帳日 10 號');
  });

  it('footer: 近 12 個月 N 筆 and ＋ 新增規則 → ?rule=new', async () => {
    const { el } = await open();
    const a = card(el, '測試卡 A');
    expect(text(a.querySelector('.rr-footer-stats'))).toBe('近 12 個月 214 筆');
    const add = a.querySelector('a.rr-add')!;
    expect(text(add)).toBe('＋ 新增規則');
    expect(add.getAttribute('href')).toBe('/accounting/accounts/11/settings?rule=new');
  });

  it('bare chips: credit accounts without rules, under 全部 only, linking to ?rule=new', async () => {
    const { el, harness } = await open();
    const bare = el.querySelector('.rr-bare')!;
    expect(text(bare.querySelector('.rr-bare-label'))).toBe('沒有規則的卡片');
    expect(texts(bare, 'a.rr-bare-chip')).toEqual(['沒規則的卡 ＋']);
    expect(bare.querySelector('a.rr-bare-chip')?.getAttribute('href')).toBe('/accounting/accounts/14/settings?rule=new');

    el.querySelectorAll<HTMLButtonElement>('.rr-filter')[1].click();
    const request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=active`);
    request.flush(OVERVIEW);
    harness.detectChanges();
    expect(el.querySelector('.rr-bare')).toBeNull();
  });

  it('no bare row when every credit account has rules', async () => {
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: OVERVIEW.accounts.slice(0, 3) });
    expect(el.querySelector('.rr-bare')).toBeNull();
  });

  it('expired banner: shown with the count, hidden under state=expired, 只看已過期 filters', async () => {
    const { el, harness } = await open();
    const banner = el.querySelector('.rr-banner')!;
    expect(banner.getAttribute('role')).toBe('status');
    expect(text(banner.querySelector('.rr-banner-text'))).toBe('1 條規則已過期但仍啟用，之後的消費不會再估算回饋');
    const button = banner.querySelector<HTMLButtonElement>('button')!;
    expect(text(button)).toBe('只看已過期');

    button.click();
    const request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=expired`);
    request.flush(OVERVIEW);
    harness.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules?state=expired');
    expect(el.querySelector('.rr-banner')).toBeNull();
    expect(text(el.querySelector('.rr-filter[aria-pressed="true"]'))).toBe('已過期 1');
  });

  it('no banner when no enabled rule has expired', async () => {
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, counts: { ...OVERVIEW.counts, enabled_expired: 0 } });
    expect(el.querySelector('.rr-banner')).toBeNull();
    expect(el.querySelector('[role="status"]')).toBeNull();
  });

  it('含已封存帳戶 adds include_archived_accounts=true and ?archived=1; unchecking drops both', async () => {
    const { harness, el } = await open();
    const box = el.querySelector<HTMLInputElement>('input.rr-archived')!;
    expect(box.checked).toBe(false);
    expect(text(box.closest('label'))).toBe('含已封存帳戶');

    box.click();
    let request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=all&include_archived_accounts=true`);
    request.flush(OVERVIEW);
    harness.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules?archived=1');
    expect(box.checked).toBe(true);

    box.click();
    request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=all`);
    request.flush(OVERVIEW);
    expect(TestBed.inject(Router).url).toBe('/accounting/reward-rules');
  });

  it('passes account_id (repeatable), state and archived from the URL', async () => {
    const { request } = await open('/accounting/reward-rules?account_id=11&account_id=12&state=disabled&archived=1');
    expect(request.request.params.getAll('account_id')).toEqual(['11', '12']);
    expect(request.request.params.get('state')).toBe('disabled');
    expect(request.request.params.get('include_archived_accounts')).toBe('true');
  });

  it('archived account names carry （已封存）', async () => {
    const archived = account(28, '舊卡', { is_archived: true, rules: [rule(95, 28, '舊')] });
    const { el } = await open('/accounting/reward-rules?archived=1', { ...OVERVIEW, accounts: [archived] });
    expect(text(el.querySelector('.rr-card-name'))).toBe('舊卡（已封存）');
  });

  it('shows 尚無回饋規則 when nothing is listed', async () => {
    const { el } = await open('/accounting/reward-rules', { ...OVERVIEW, accounts: [] });
    expect(el.querySelector('.rr-card')).toBeNull();
    expect(text(el.querySelector('.rr-none'))).toBe('尚無回饋規則');
  });

  it('the back link ‹ 記帳設定 is present at desktop width and leaves to /accounting/settings', async () => {
    const { el } = await open();
    const back = el.querySelector<HTMLButtonElement>('.rr-back')!;
    expect(text(back)).toBe('‹ 記帳設定');
    back.click();
    await vi.waitFor(() => expect(TestBed.inject(Router).url).toBe('/accounting/settings'));
  });

  it('shows a load failure in role=alert', async () => {
    TestBed.inject(LayoutModeService).set('panes');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/accounting/reward-rules');
    harness.detectChanges();
    overviewRequest().flush({ detail: '讀取失敗了' }, { status: 500, statusText: 'Server Error' });
    harness.detectChanges();
    const el = harness.routeNativeElement as HTMLElement;
    expect(text(el.querySelector('[role="alert"]'))).toBe('讀取失敗了');
    expect(el.querySelector('.rr-grid')?.getAttribute('aria-busy')).toBeNull();
  });

  it('reloads after a reward rule write elsewhere (accountsChanged)', async () => {
    const { harness } = await open();
    TestBed.inject(AccountingService).deleteRewardRule(11, 3).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/accounts/11/reward-rules/3').flush(null);
    harness.detectChanges();
    const request = await nextRequest(harness);
    expect(request.request.urlWithParams).toBe(`${URL}?state=all`);
    request.flush(OVERVIEW);
  });
});
