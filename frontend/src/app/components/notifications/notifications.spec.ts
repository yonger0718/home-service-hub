import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { HttpErrorResponse } from '@angular/common/http';
import { computed, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { NEVER, Subject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ScheduleCatchUpResult, ScheduleInstance } from '../../models/accounting.model';
import { AccountingService } from '../../services/accounting.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { ToastService } from '../../services/toast.service';
import { IMPORT_RUNNING_TOAST } from '../accounting/schedule-math';
import { NotificationCenterService } from '../../services/notification-center.service';
import { PortfolioService } from '../../services/portfolio.service';
import { notificationsMatcher } from '../../app.routes';
import { LayoutMode, LayoutModeService, MATCH_MEDIA, PHONE_QUERY } from '../../services/layout-mode.service';
import { InboxService } from '../accounting/inbox/inbox.service';
import { NotifSourceKey, NotifSummary, NotifTask, NotifTier, NotifTrackingRow, summarize } from './notif-task';
import { NOTIF_PRIMARY_CONTROL, NotifRowComponent } from './notif-row/notif-row';
import { ReconFlowComponent } from './recon-flow/recon-flow';
import { NotificationsComponent } from './notifications';

function task(id: string, tier: NotifTier, over: Partial<NotifTask> = {}): NotifTask {
  return {
    id,
    source: 'ledger',
    from: 'bills',
    tier,
    icon: 'pi pi-credit-card',
    title: `測試任務 ${id}`,
    when: '今天',
    meta: '測試銀行卡',
    amount: 'NT$ 100',
    progress: null,
    primary: { label: '記繳款', route: '/accounting/accounts/1' },
    secondary: [],
    sortKey: '2026-10-24',
    fingerprint: id,
    ...over,
  };
}

/** The primary control of the row showing task `id`. NOTIF_PRIMARY_CONTROL is a selector list: look inside the row. */
function rowPrimary(root: ParentNode, id: string): HTMLElement | null {
  return root.querySelector(`app-notif-row[data-task-id="${id}"]`)?.querySelector<HTMLElement>(NOTIF_PRIMARY_CONTROL) ?? null;
}

const TASKS: NotifTask[] = [
  task('a1', 'overdue', { sortKey: '2026-10-01' }),
  task('a2', 'overdue', { sortKey: '2026-10-02' }),
  task('b1', 'today'),
  task('c1', 'confirm'),
  task('d1', 'system', { source: 'portfolio', from: 'importReview' }),
];
const ROWS: NotifTrackingRow[] = [
  { id: 'debts', source: 'ledger', title: '借還款 · 3 位', meta: '甲、乙、丙', count: 3, link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'debts' } } },
  { id: 'upcoming', source: 'ledger', title: '即將到來 · 2 件', meta: '', count: 2, link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'cards' } } },
];

describe('NotificationsComponent', () => {
  const tasks = signal<readonly NotifTask[]>(TASKS);
  const trackingRows = signal<readonly NotifTrackingRow[]>(ROWS);
  const handled = signal<string | null>('3 件');
  const loaded = signal(true);
  const round = signal(1);
  const settledRound = signal(1);
  const truncated = signal(false);
  const overflow = signal<NotifTask | null>(null);
  const failedSources = signal<readonly NotifSourceKey[]>([]);
  const nextUpcoming = signal<NotifTask | null>(null);
  const cathayMore = signal(0);
  const refresh = vi.fn();
  /** As the service: starts the next round and returns its id. */
  const invalidate = vi.fn(() => {
    round.update(r => r + 1);
    return round();
  });
  let width = 1440;

  /** One Subject per write call, in call order. */
  let posts: Subject<ScheduleInstance>[] = [];
  let catchUps: Subject<ScheduleCatchUpResult>[] = [];
  const accounting = {
    postScheduleInstance: vi.fn((_id: number) => {
      const s = new Subject<ScheduleInstance>();
      posts.push(s);
      return s.asObservable();
    }),
    catchUpSchedule: vi.fn((_id: number) => {
      const s = new Subject<ScheduleCatchUpResult>();
      catchUps.push(s);
      return s.asObservable();
    }),
  };
  /** One Subject per 確認, in call order. */
  let acks: Subject<{ trade_date: string; ack_status: string }>[] = [];
  const portfolio = {
    ackCathayDay: vi.fn((_tradeDate: string) => {
      const s = new Subject<{ trade_date: string; ack_status: string }>();
      acks.push(s);
      return s.asObservable();
    }),
  };

  const stub = {
    tasks,
    trackingRows,
    handled,
    loaded,
    loading: computed(() => round() > settledRound()),
    round,
    settledRound,
    truncated,
    overflow,
    failedSources,
    nextUpcoming,
    cathayMore,
    summary: computed<NotifSummary>(() => summarize(tasks())),
    badges: signal({}),
    refresh,
    invalidate,
  };

  let fixture: ComponentFixture<NotificationsComponent>;
  const settle = async () => {
    await fixture.whenStable();
    fixture.detectChanges();
  };

  function mount(w = 1440, before?: () => void): HTMLElement {
    width = w;
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: NotificationCenterService, useValue: stub },
        { provide: AccountingService, useValue: accounting },
        { provide: PortfolioService, useValue: portfolio },
        {
          provide: MATCH_MEDIA,
          useValue: (q: string) =>
            ({ matches: q === PHONE_QUERY ? width < 760 : false, media: q, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList,
        },
      ],
    });
    before?.();
    fixture = TestBed.createComponent(NotificationsComponent);
    fixture.autoDetectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null | undefined) => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const subtitle = (root: HTMLElement) => text(root.querySelector('.page-subtitle'));

  beforeEach(() => {
    tasks.set(TASKS);
    trackingRows.set(ROWS);
    handled.set('3 件');
    loaded.set(true);
    round.set(1);
    settledRound.set(1);
    truncated.set(false);
    overflow.set(null);
    failedSources.set([]);
    nextUpcoming.set(null);
    cathayMore.set(0);
    refresh.mockClear();
    invalidate.mockClear();
    accounting.postScheduleInstance.mockClear();
    accounting.catchUpSchedule.mockClear();
    posts = [];
    catchUps = [];
    portfolio.ackCathayDay.mockClear();
    acks = [];
  });
  afterEach(() => TestBed.resetTestingModule());

  it('opening the page asks for a refresh', () => {
    mount();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledWith({ reason: 'open' });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('has one h1 and sections in the order 已逾期, 今天, 要你確認, 系統 with their counts', () => {
    const root = mount();
    expect(root.querySelectorAll('h1').length).toBe(1);
    const sections = Array.from(root.querySelectorAll('section.notif-section'));
    expect(sections.map(s => text(s.querySelector('h2')))).toEqual(['已逾期 2', '今天 1', '要你確認 1', '系統 1']);
    for (const s of sections) {
      const id = s.getAttribute('aria-labelledby')!;
      expect(s.querySelector(`h2#${id}`)).not.toBeNull();
      expect(s.querySelector('ul.notif-card')).not.toBeNull();
      expect(s.querySelectorAll('ul.notif-card > li').length).toBeGreaterThan(0);
    }
  });

  it('a tier without tasks has no section', () => {
    tasks.set([task('b1', 'today'), task('d1', 'system')]);
    const root = mount();
    expect(Array.from(root.querySelectorAll('section.notif-section h2')).map(h => text(h))).toEqual(['今天 1', '系統 1']);
  });

  it("rows keep the service's order inside a section", () => {
    tasks.set([task('z', 'overdue'), task('y', 'overdue'), task('x', 'overdue')]);
    const root = mount();
    const ids = Array.from(root.querySelectorAll('app-notif-row')).map(r => r.getAttribute('data-task-id'));
    expect(ids).toEqual(['z', 'y', 'x']);
  });

  describe('subtitle', () => {
    const many = (overdue: number, today: number) => [
      ...Array.from({ length: overdue }, (_, i) => task(`o${i}`, 'overdue')),
      ...Array.from({ length: today }, (_, i) => task(`t${i}`, 'today')),
    ];
    it('reads in full at 1440', () => {
      tasks.set(many(3, 4));
      expect(subtitle(mount(1440))).toBe('7 件要處理 · 3 件已逾期 · 數字只算要你動手的事');
    });
    it('drops the last part at 390', () => {
      tasks.set(many(3, 1));
      expect(subtitle(mount(390))).toBe('4 件要處理 · 3 件已逾期');
    });
    it('has no 已逾期 part when none is overdue', () => {
      tasks.set(many(0, 2));
      expect(subtitle(mount(1440))).toBe('2 件要處理 · 數字只算要你動手的事');
    });
    it('has no 已逾期 part at 390 either', () => {
      tasks.set(many(0, 1));
      expect(subtitle(mount(390))).toBe('1 件要處理');
    });
    it('reads 沒有要處理的事 at 0', () => {
      tasks.set([]);
      expect(subtitle(mount(1440))).toBe('沒有要處理的事');
    });
  });

  describe('before the first read has settled', () => {
    it('shows no all-clear, no counts, no sections, no 追蹤中 and no note', async () => {
      loaded.set(false);
      tasks.set([]);
      const root = mount();
      expect(subtitle(root)).toBe('讀取中…');
      expect(root.textContent).not.toContain('沒有要處理的事');
      const chips = Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button'));
      expect(chips.map(c => text(c))).toEqual(['全部', '財務', '投資']);
      expect(chips.map(c => c.getAttribute('aria-label'))).toEqual(['全部', '財務', '投資']);
      expect(root.querySelector('.chip-count')).toBeNull();
      expect(root.querySelector('.notif-none, section.notif-section, details.notif-tracking')).toBeNull();
      tasks.set(TASKS);
      loaded.set(true);
      await settle();
      expect(text(root.querySelector('.notif-chips button'))).toBe('全部5');
      expect(subtitle(root)).toContain('5 件要處理');
      expect(root.querySelectorAll('app-notif-row').length).toBe(5);
    });
    it('the section headings carry no number until loaded, then the count', async () => {
      loaded.set(false);
      tasks.set(TASKS);
      const root = mount();
      expect(Array.from(root.querySelectorAll('section.notif-section h2')).map(h => text(h))).toEqual(['已逾期', '今天', '要你確認', '系統']);
      expect(root.querySelector('.sec-count')).toBeNull();
      loaded.set(true);
      await settle();
      expect(Array.from(root.querySelectorAll('section.notif-section h2')).map(h => text(h))).toEqual(['已逾期 2', '今天 1', '要你確認 1', '系統 1']);
    });
    it('the section headings keep their numbers while ONE source failed (the sidebar says the same)', () => {
      tasks.set(TASKS);
      failedSources.set(['recon']);
      const root = mount();
      expect(Array.from(root.querySelectorAll('section.notif-section h2')).map(h => text(h))).toEqual(['已逾期 2', '今天 1', '要你確認 1', '系統 1']);
    });
    it('the section headings keep their numbers with MANY sources failed while tasks are counted', () => {
      tasks.set(TASKS);
      failedSources.set(['bills', 'schedules', 'recon', 'importReview', 'debts']);
      const root = mount();
      expect(Array.from(root.querySelectorAll('section.notif-section h2')).map(h => text(h))).toEqual(['已逾期 2', '今天 1', '要你確認 1', '系統 1']);
    });
    it('hides 追蹤中 that the service already holds', () => {
      loaded.set(false);
      const root = mount();
      expect(root.querySelector('details.notif-tracking')).toBeNull();
    });
  });

  describe('states: loading, empty, a failed source', () => {
    const BILL_NEXT = task('bill:11:2026-10-27', 'tracking', {
      title: '測試銀行卡帳單',
      when: '10/27 截止',
      amount: '$1,824',
    });
    const CATCH_ALL = task('recon:more', 'confirm', { from: 'recon', title: '還有 12 筆對帳項目未列出', when: '', primary: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'todo' } } });
    const q = (root: HTMLElement, sel: string) => root.querySelector<HTMLElement>(sel);

    it('before the first answers: 讀取中… as a polite status, no 都處理好了, chips still there', () => {
      loaded.set(false);
      tasks.set([]);
      const root = mount();
      const loading = q(root, 'p.notif-loading')!;
      expect(text(loading)).toBe('讀取中…');
      expect(loading.getAttribute('role')).toBe('status');
      expect(q(root, '.notif-empty')).toBeNull();
      expect(root.querySelectorAll('.notif-chips button').length).toBe(3);
      expect(q(root, 'a.notif-reminders')).not.toBeNull();
    });

    it('the loading row is the same element when a round starts and settles around it (not re-announced)', async () => {
      loaded.set(false);
      tasks.set([]);
      const root = mount();
      const before = q(root, 'p.notif-loading');
      round.set(2);
      await settle();
      expect(q(root, 'p.notif-loading')).toBe(before);
    });

    it('loaded and nothing to do: 都處理好了 (h2, check icon), the next bill and the tracking link', () => {
      tasks.set([]);
      nextUpcoming.set(BILL_NEXT);
      const root = mount();
      const empty = q(root, 'div.notif-empty')!;
      expect(empty.querySelector('i.pi.pi-check-circle')).not.toBeNull();
      expect(text(empty.querySelector('h2'))).toBe('都處理好了');
      expect(text(q(root, 'p.empty-next'))).toBe('下一件：測試銀行卡帳單 · 10/27 截止 · $1,824');
      expect(text(q(root, 'button.empty-tracking'))).toBe('看追蹤中的 5 件 ›');
      expect(q(root, '.notif-loading')).toBeNull();
      expect(subtitle(root)).toBe('沒有要處理的事');
    });

    describe('a later round in flight', () => {
      const digits = (root: HTMLElement) => [...root.querySelectorAll('.notif-chips button')].map(b => text(b)).join('|');

      it('no task and a round in flight: 讀取中…, no all-clear, no zero chips; settled with zero tasks the all-clear returns', async () => {
        tasks.set([]);
        const root = mount();
        expect(q(root, '.notif-empty')).not.toBeNull();
        round.set(2);
        await settle();
        expect(q(root, '.notif-empty')).toBeNull();
        expect(root.textContent).not.toContain('都處理好了');
        expect(subtitle(root)).toBe('讀取中…');
        expect(digits(root)).not.toMatch(/\d/);
        expect(root.querySelectorAll('p.notif-loading[role="status"]').length).toBe(1);
        expect(q(root, 'details.notif-tracking')).toBeNull();
        settledRound.set(2);
        await settle();
        expect(q(root, '.notif-empty')).not.toBeNull();
        expect(subtitle(root)).toBe('沒有要處理的事');
        expect(q(root, '.notif-loading')).toBeNull();
        expect(digits(root)).toMatch(/0/);
      });

      it('tasks present and a round in flight: rows, numbers and subtitle stay as the last settled round left them', async () => {
        const root = mount();
        const before = { sub: subtitle(root), chips: digits(root), rows: root.querySelectorAll('app-notif-row').length };
        round.set(2);
        await settle();
        expect(subtitle(root)).toBe(before.sub);
        expect(digits(root)).toBe(before.chips);
        expect(root.querySelectorAll('app-notif-row').length).toBe(before.rows);
        expect(q(root, '.notif-loading')).toBeNull();
        expect(q(root, '.sec-count')).not.toBeNull();
      });

      it('focus on 看追蹤中 when the all-clear leaves goes to the checked chip', async () => {
        tasks.set([]);
        const root = mount();
        const link = q(root, 'button.empty-tracking') as HTMLButtonElement;
        link.focus();
        expect(document.activeElement).toBe(link);
        round.set(2);
        await settle();
        expect(q(root, '.notif-empty')).toBeNull();
        expect(document.activeElement).toBe(q(root, '.notif-chips button[aria-checked="true"]'));
      });
    });

    it('看追蹤中 opens the 追蹤中 block and focuses its summary', async () => {
      tasks.set([]);
      const root = mount();
      const details = q(root, 'details.notif-tracking') as HTMLDetailsElement;
      expect(details.open).toBe(false);
      // A real button: Enter and Space activate it natively, with no href to follow.
      expect(q(root, 'a.empty-tracking')).toBeNull();
      const link = q(root, 'button.empty-tracking') as HTMLButtonElement;
      expect(link.type).toBe('button');
      link.click();
      await settle();
      expect(details.open).toBe(true);
      expect(document.activeElement).toBe(details.querySelector('summary'));
    });

    it('nothing to do and nothing tracked: 都處理好了 alone', () => {
      tasks.set([]);
      trackingRows.set([]);
      const root = mount();
      expect(text(q(root, '.notif-empty h2'))).toBe('都處理好了');
      expect(q(root, 'p.empty-next')).toBeNull();
      expect(q(root, 'button.empty-tracking')).toBeNull();
      expect(q(root, 'details.notif-tracking')).toBeNull();
    });

    it('only tracking rows: the all-clear shows with the tracking link, and 追蹤中 below it', () => {
      tasks.set([]);
      const root = mount();
      expect(q(root, '.notif-empty')).not.toBeNull();
      expect(q(root, 'details.notif-tracking')).not.toBeNull();
    });

    it('a chip chosen while empty keeps the all-clear and adds no 「這個來源…」 note', async () => {
      tasks.set([]);
      const root = mount();
      root.querySelectorAll<HTMLButtonElement>('.notif-chips button')[2].click();
      await settle();
      expect(q(root, '.notif-empty')).not.toBeNull();
      expect(q(root, '.notif-none')).toBeNull();
    });

    it('with tasks there is no all-clear and no loading row', () => {
      const root = mount();
      expect(q(root, '.notif-empty')).toBeNull();
      expect(q(root, '.notif-loading')).toBeNull();
    });

    it('a failed inbox: 「無法讀取：對帳」 is the last element of the page, the other tasks stay', () => {
      failedSources.set(['recon']);
      const root = mount();
      const page = q(root, '.notif-page')!;
      const last = page.lastElementChild!;
      expect(last.classList.contains('notif-failed')).toBe(true);
      expect(text(last)).toBe('無法讀取：對帳 重試');
      expect(root.querySelectorAll('app-notif-row').length).toBe(5);
    });

    it('two failed sources read 「無法讀取：帳單、對帳」', () => {
      failedSources.set(['bills', 'recon']);
      const root = mount();
      expect(text(q(root, '.notif-failed .banner-title'))).toBe('無法讀取：帳單、對帳');
    });

    it('a failed source with no task left: no 都處理好了, no 讀取中… row, the subtitle 「部分資料無法讀取」; the failure line, chips and links show', () => {
      tasks.set([]);
      failedSources.set(['recon']);
      const root = mount();
      expect(q(root, '.notif-empty')).toBeNull();
      expect(q(root, '.notif-loading')).toBeNull();
      expect(root.textContent).not.toContain('沒有要處理的事');
      expect(root.textContent).not.toContain('都處理好了');
      expect(subtitle(root)).toBe('部分資料無法讀取');
      expect(text(q(root, '.notif-failed'))).toBe('無法讀取：對帳 重試');
      expect(root.querySelectorAll('.notif-chips button').length).toBe(3);
      expect(q(root, 'details.notif-tracking')).not.toBeNull();
    });

    it('the failure line is the shared banner: an alert, text 「無法讀取：帳單、對帳」, button 重試; its text node survives unrelated changes', async () => {
      failedSources.set(['bills', 'recon']);
      const root = mount();
      const banner = q(root, 'app-banner.notif-failed')!;
      expect(banner.querySelector('.banner--error[role="alert"]')).not.toBeNull();
      expect(text(banner.querySelector('.banner-action'))).toBe('重試');
      const title = banner.querySelector('.banner-title')!;
      const node = title.firstChild;
      root.querySelectorAll<HTMLButtonElement>('.notif-chips button')[1].click();
      tasks.set([...TASKS, task('z9', 'today')]);
      await settle();
      expect(q(root, 'app-banner.notif-failed')).toBe(banner);
      expect(banner.querySelector('.banner-title')!.firstChild).toBe(node);
    });

    it('one failed source: the rows render, the subtitle is 「部分資料無法讀取 · 5 件要處理 · 2 件已逾期」 and the chips carry their numbers', () => {
      failedSources.set(['recon']);
      const root = mount();
      expect(root.querySelectorAll('app-notif-row').length).toBe(5);
      expect(subtitle(root)).toBe('部分資料無法讀取 · 5 件要處理 · 2 件已逾期');
      const chips = Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button'));
      expect(chips.map(c => text(c))).toEqual(['全部5', '財務4', '投資1']);
      expect(chips.map(c => c.getAttribute('aria-label'))).toEqual(['全部，5 件', '財務，4 件', '投資，1 件']);
    });

    it('every source failed and nothing counted: no number anywhere, the subtitle is 「部分資料無法讀取」, chip names are bare labels', () => {
      tasks.set([]);
      failedSources.set(['bills', 'schedules', 'recon', 'importReview', 'debts']);
      const root = mount();
      expect(subtitle(root)).toBe('部分資料無法讀取');
      const chips = Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button'));
      expect(chips.map(c => text(c))).toEqual(['全部', '財務', '投資']);
      expect(chips.map(c => c.getAttribute('aria-label'))).toEqual(['全部', '財務', '投資']);
      expect(q(root, '.chip-count')).toBeNull();
    });

    it('one failed source and nothing counted: the subtitle is 「部分資料無法讀取」 alone (no 0 件, no all-clear)', () => {
      tasks.set([]);
      failedSources.set(['recon']);
      const root = mount();
      expect(subtitle(root)).toBe('部分資料無法讀取');
      expect(root.textContent).not.toContain('0 件');
      const chips = Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button'));
      expect(root.querySelectorAll('.chip-count').length).toBe(0);
      expect(chips.map(c => c.getAttribute('aria-label'))).toEqual(['全部', '財務', '投資']);
      expect(root.querySelectorAll('.sec-count').length).toBe(0);
    });

    it('one failed source and tasks counted: subtitle, chip counts and section headings all carry the numbers', () => {
      tasks.set([task('a1', 'today'), task('a2', 'overdue')]);
      failedSources.set(['bills', 'schedules', 'recon', 'importReview']);
      const root = mount();
      expect(subtitle(root)).toContain('部分資料無法讀取 · 2 件要處理 · 1 件已逾期');
      expect(Array.from(root.querySelectorAll('.chip-count')).map(c => text(c))[0]).toBe('2');
      expect(root.querySelector('.notif-chips button')?.getAttribute('aria-label')).toBe('全部，2 件');
      expect(root.querySelectorAll('.sec-count').length).toBeGreaterThan(0);
    });

    it('one failed source with overdue tasks: the overdue part stays, 「部分資料無法讀取 · 3 件要處理 · 2 件已逾期」', () => {
      tasks.set([task('a1', 'today'), task('a2', 'overdue'), task('a3', 'overdue')]);
      failedSources.set(['recon']);
      const root = mount();
      expect(subtitle(root)).toBe('部分資料無法讀取 · 3 件要處理 · 2 件已逾期');
    });

    it('one failed source and no overdue task: no overdue part, 「部分資料無法讀取 · 2 件要處理」', () => {
      tasks.set([task('a1', 'today'), task('a2', 'confirm')]);
      failedSources.set(['recon']);
      expect(subtitle(mount())).toBe('部分資料無法讀取 · 2 件要處理');
    });

    it('after a 重試 that succeeds the counts and the subtitle come back', async () => {
      failedSources.set(['recon']);
      const root = mount();
      (q(root, '.notif-failed .banner-action') as HTMLButtonElement).click();
      await settle();
      expect(subtitle(root)).toBe('部分資料無法讀取 · 5 件要處理 · 2 件已逾期');
      failedSources.set([]);
      settledRound.set(round());
      await settle();
      expect(text(root.querySelector('.notif-chips button'))).toBe('全部5');
      expect(subtitle(root)).toContain('5 件要處理');
    });

    it('two presses on 重試 call invalidate once', async () => {
      failedSources.set(['recon']);
      const root = mount();
      const button = q(root, '.notif-failed .banner-action') as HTMLButtonElement;
      button.click();
      button.click();
      await settle();
      expect(invalidate).toHaveBeenCalledTimes(1);
    });

    it('under the 投資 chip the all-clear hides the next-bill line and the tracking link', async () => {
      tasks.set([]);
      nextUpcoming.set(BILL_NEXT);
      const root = mount();
      expect(q(root, '.empty-next')).not.toBeNull();
      root.querySelectorAll<HTMLButtonElement>('.notif-chips button')[2].click();
      await settle();
      expect(q(root, '.notif-empty h2')).not.toBeNull();
      expect(q(root, '.empty-next')).toBeNull();
      expect(q(root, '.empty-tracking')).toBeNull();
    });

    it('重試 calls invalidate(); the line stays and the button is disabled while loading; the line leaves on recovery', async () => {
      failedSources.set(['recon']);
      settledRound.set(1);
      const root = mount();
      const line = q(root, '.notif-failed');
      const button = q(root, '.notif-failed .banner-action') as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      button.click();
      await settle();
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(q(root, '.notif-failed')).toBe(line);
      expect((q(root, '.notif-failed .banner-action') as HTMLButtonElement).disabled).toBe(true);
      failedSources.set([]);
      settledRound.set(round());
      await settle();
      expect(q(root, '.notif-failed')).toBeNull();
    });

    it('a retry that succeeds hands focus to the chosen chip when 重試 held it', async () => {
      failedSources.set(['recon']);
      const root = mount();
      const button = q(root, '.notif-failed .banner-action') as HTMLButtonElement;
      button.focus();
      button.click();
      await settle();
      failedSources.set([]);
      settledRound.set(round());
      await settle();
      expect(q(root, '.notif-failed')).toBeNull();
      expect(document.activeElement).toBe(q(root, '.notif-chips button[aria-checked="true"]'));
    });

    it('a retry that fails again keeps the line and returns focus to 重試 when focus fell to <body>', async () => {
      failedSources.set(['recon']);
      const root = mount();
      const button = q(root, '.notif-failed .banner-action') as HTMLButtonElement;
      button.focus();
      button.click();
      await settle();
      // A browser may drop focus from the button it just disabled.
      const tmp = document.createElement('button');
      document.body.append(tmp);
      tmp.focus();
      tmp.remove();
      expect(document.activeElement).toBe(document.body);
      settledRound.set(round());
      await settle();
      expect(q(root, '.notif-failed')).not.toBeNull();
      expect(document.activeElement).toBe(q(root, '.notif-failed .banner-action'));
    });

    it('a retry that fails again leaves focus the owner moved elsewhere alone', async () => {
      failedSources.set(['recon']);
      const root = mount();
      (q(root, '.notif-failed .banner-action') as HTMLButtonElement).click();
      await settle();
      const chip = q(root, '.notif-chips button')!;
      chip.focus();
      settledRound.set(round());
      await settle();
      expect(document.activeElement).toBe(chip);
    });

    it('truncated: the catch-all is a plain note right under the sections, not a row, not in a numbered section, not counted', () => {
      tasks.set([task('b1', 'today')]);
      truncated.set(true);
      overflow.set(CATCH_ALL);
      failedSources.set(['bills']);
      const root = mount();
      const note = q(root, 'p.notif-overflow')!;
      expect(text(note)).toBe('還有 12 筆對帳項目未列出 · 到提醒中心查看 ›');
      expect(text(note)).not.toContain('不計入數字');
      const link = note.querySelector('a')!;
      expect(link.classList.contains('notif-foot-link')).toBe(true);
      expect(text(link)).toBe('到提醒中心查看 ›');
      expect(link.querySelector('span')?.getAttribute('aria-hidden')).toBe('true');
      expect(note.previousElementSibling?.matches('section.notif-section')).toBe(true);
      // Not a row and not inside a section: the numbers stay the rows' numbers.
      expect(note.closest('section.notif-section')).toBeNull();
      expect(root.querySelectorAll('app-notif-row').length).toBe(1);
      expect(text(q(root, 'section.notif-section h2'))).toBe('今天 1');
      expect(note.querySelector('a')?.getAttribute('href')).toBe('/accounting/reminders?tab=todo');
    });

    it('truncated with no counted task: the note shows, 都處理好了 does not, the subtitle does not claim an all-clear or a count', () => {
      tasks.set([]);
      truncated.set(true);
      overflow.set(CATCH_ALL);
      const root = mount();
      expect(root.querySelectorAll('app-notif-row').length).toBe(0);
      expect(q(root, '.notif-empty')).toBeNull();
      expect(q(root, 'p.notif-overflow')).not.toBeNull();
      expect(subtitle(root)).toBe('還有對帳項目未列出');
      expect(text(root.querySelector('.notif-chips button'))).toBe('全部0');
    });

    it('the catch-all is a ledger thing: hidden under 投資', async () => {
      tasks.set([task('b1', 'today')]);
      truncated.set(true);
      overflow.set(CATCH_ALL);
      const root = mount();
      (root.querySelectorAll('.notif-chips button')[2] as HTMLButtonElement).click();
      await settle();
      expect(q(root, 'p.notif-overflow')).toBeNull();
    });

    it('truncated with an unknown number: 「還有對帳項目未列出 · 到提醒中心查看 ›」', () => {
      truncated.set(true);
      overflow.set({ ...CATCH_ALL, title: '還有對帳項目未列出' });
      const root = mount();
      expect(text(q(root, 'p.notif-overflow'))).toBe('還有對帳項目未列出 · 到提醒中心查看 ›');
    });

    it('not truncated: no such line', () => {
      const root = mount();
      expect(q(root, '.notif-overflow')).toBeNull();
    });

    it('tasks arriving while another source is still loading render at once, with 讀取中… after the sections (one element)', async () => {
      loaded.set(false);
      tasks.set([]);
      const root = mount();
      const before = q(root, 'p.notif-loading')!;
      expect(before).not.toBeNull();
      tasks.set([task('b1', 'today')]);
      await settle();
      expect(root.querySelectorAll('app-notif-row').length).toBe(1);
      const after = q(root, 'p.notif-loading')!;
      expect(after).toBe(before);
      expect(after.getAttribute('role')).toBe('status');
      expect(root.querySelectorAll('[role="status"]').length).toBe(1);
      const section = q(root, 'section.notif-section')!;
      expect(section.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(q(root, '.notif-empty')).toBeNull();
      // Counts stay hidden until a round settles, so they never show a low number that then grows.
      expect(q(root, '.chip-count')).toBeNull();

      loaded.set(true);
      await settle();
      expect(q(root, '.notif-loading')).toBeNull();
      expect(root.querySelectorAll('app-notif-row').length).toBe(1);
    });

    it('a later round keeps the list on screen while it loads', async () => {
      const root = mount();
      round.set(2);
      await settle();
      expect(root.querySelectorAll('app-notif-row').length).toBe(5);
      expect(q(root, '.notif-loading')).toBeNull();
      expect(q(root, '.notif-empty')).toBeNull();
    });

    it('重試 pressed while a row is busy: the retry\'s round releases it only once it settles; an older round does not', async () => {
      const rent = task('sched:1', 'today', {
        from: 'schedules',
        primary: { label: '入帳', command: { kind: 'postInstance', instanceId: 7, name: '測試房租' } },
      });
      tasks.set([rent]);
      failedSources.set(['recon']);
      round.set(4);
      settledRound.set(3);
      const root = mount();
      const control = () => rowPrimary(root, 'sched:1') as HTMLButtonElement;
      const retryButton = () => q(root, '.notif-failed .banner-action') as HTMLButtonElement;
      control().click();
      await settle();
      posts[0].next({} as ScheduleInstance);
      posts[0].complete(); // answered while round 4 is the newest: the row waits for a round above 4
      await settle();
      expect(retryButton().disabled).toBe(true); // round 4 still in flight
      settledRound.set(4); // the older round settles: no release
      await settle();
      expect(control().disabled).toBe(true);
      expect(retryButton().disabled).toBe(false);
      retryButton().click();
      await settle();
      expect(invalidate).toHaveReturnedWith(5);
      expect(round()).toBe(5);
      expect(control().disabled).toBe(true); // round 5 unsettled
      settledRound.set(5);
      await settle();
      expect(control().disabled).toBe(false);
    });
  });

  describe('chips', () => {
    const chips = (root: HTMLElement) => Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button[role="radio"]'));

    it('is a labelled single-select radiogroup with counts: 全部 5 / 財務 4 / 投資 1, 全部 checked', () => {
      const root = mount();
      expect(root.querySelector('.notif-chips')?.getAttribute('role')).toBe('radiogroup');
      expect(root.querySelector('.notif-chips')?.getAttribute('aria-label')).toBe('來源');
      expect(chips(root).map(c => text(c))).toEqual(['全部5', '財務4', '投資1']);
      expect(chips(root).map(c => c.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
      expect(root.querySelectorAll('.chip-count').length).toBe(3);
    });

    it('counts never include tracking rows', () => {
      const root = mount();
      expect(text(chips(root)[0])).toBe('全部5');
    });

    it('picking 投資 shows only the 投資 task and hides 追蹤中', async () => {
      const root = mount();
      chips(root)[2].click();
      await settle();
      expect(chips(root).map(c => c.getAttribute('aria-checked'))).toEqual(['false', 'false', 'true']);
      expect(Array.from(root.querySelectorAll('app-notif-row')).map(r => r.getAttribute('data-task-id'))).toEqual(['d1']);
      expect(root.querySelector('p.notif-none')).toBeNull();
      expect(root.querySelector('details.notif-tracking')).toBeNull();
    });

    it('picking 財務 shows the ledger rows and keeps 追蹤中', async () => {
      const root = mount();
      chips(root)[1].click();
      await settle();
      expect(Array.from(root.querySelectorAll('app-notif-row')).map(r => r.getAttribute('data-task-id'))).toEqual(['a1', 'a2', 'b1', 'c1']);
      expect(root.querySelector('p.notif-none')).toBeNull();
      expect(root.querySelector('details.notif-tracking')).not.toBeNull();
    });

    it('a chosen chip whose count drops to 0 after a refresh shows the note; everything at 0 does not', async () => {
      const root = mount();
      chips(root)[2].click();
      await settle();
      tasks.set(TASKS.filter(t => t.source === 'ledger'));
      await settle();
      expect(text(chips(root)[2])).toBe('投資0');
      expect(root.querySelectorAll('app-notif-row').length).toBe(0);
      expect(text(root.querySelector('p.notif-none'))).toBe('這個來源目前沒有要處理的事');
      // Everything done: the note goes too (nothing else shows in its place).
      tasks.set([]);
      await settle();
      expect(root.querySelector('p.notif-none')).toBeNull();
    });

    it('keeps focus on the chip that was pressed while the list changes', async () => {
      const root = mount();
      document.body.appendChild(root);
      const third = chips(root)[2];
      third.focus();
      third.click();
      await settle();
      expect(root.querySelectorAll('app-notif-row').length).toBe(1);
      expect(document.activeElement).toBe(chips(root)[2]);
      expect(chips(root)[2]).toBe(third);
      root.remove();
    });

    it('a chip with a count has an accessible name that includes it', () => {
      const root = mount();
      expect(chips(root)[1].getAttribute('aria-label')).toBe('財務，4 件');
    });
  });

  describe('追蹤中', () => {
    it('is folded by default and lists the rows with 查看 links', () => {
      const root = mount();
      const d = root.querySelector('details.notif-tracking') as HTMLDetailsElement;
      expect(d.open).toBe(false);
      const rows = Array.from(d.querySelectorAll('.tracking-row'));
      expect(rows.map(r => text(r.querySelector('.tracking-title')))).toEqual(['借還款 · 3 位', '即將到來 · 2 件']);
      const links = Array.from(d.querySelectorAll('a.tracking-link'));
      expect(links.map(a => a.getAttribute('href'))).toEqual(['/accounting/reminders?tab=debts', '/accounting/reminders?tab=cards']);
      expect(links.map(a => a.textContent?.trim())).toEqual(['查看', '查看']);
      expect(links[0].getAttribute('aria-label')).toBe('查看：借還款 · 3 位');
    });

    it('summary reads 追蹤中 · 不計入數字 and 借還款 3 位 · 即將到來 2 件', () => {
      const root = mount();
      expect(text(root.querySelector('details.notif-tracking summary'))).toBe('追蹤中 · 不計入數字 借還款 3 位 · 即將到來 2 件');
      expect(text(root.querySelector('.tracking-sum'))).toBe('借還款 3 位 · 即將到來 2 件');
    });

    it('leaves out the part of an absent row, and renders no meta line for an empty meta', () => {
      trackingRows.set([ROWS[1]]);
      const root = mount();
      expect(text(root.querySelector('.tracking-sum'))).toBe('即將到來 2 件');
      expect(root.querySelector('.tracking-meta')).toBeNull();
    });

    it('is absent without rows', () => {
      trackingRows.set([]);
      expect(mount().querySelector('details.notif-tracking')).toBeNull();
    });

    it('renders a meta line when there is one', () => {
      const root = mount();
      expect(text(root.querySelector('.tracking-meta'))).toBe('甲、乙、丙');
    });
  });

  describe('links', () => {
    it('handled link: 已處理的對帳（近 7 天）3 件 › → /accounting/reminders?tab=todo', () => {
      const a = mount().querySelector('a.notif-handled')!;
      expect(text(a)).toMatch(/^已處理的對帳（近 7 天）3 件\s?›$/);
      expect(a.getAttribute('href')).toBe('/accounting/reminders?tab=todo');
    });
    it('handled link is absent when handled is null', () => {
      handled.set(null);
      expect(mount().querySelector('a.notif-handled')).toBeNull();
    });
    it('the 提醒中心 link is always there', () => {
      const a = mount().querySelector('a.notif-reminders')!;
      expect(text(a)).toMatch(/^提醒中心（借還款、週期與分期管理）\s?›$/);
      expect(a.getAttribute('href')).toBe('/accounting/reminders');
    });
    it('both footer links are one link style: the shared class, a decorative › at the end, the text as the accessible name', () => {
      const root = mount();
      const links = [root.querySelector('a.notif-handled')!, root.querySelector('a.notif-reminders')!];
      for (const a of links) {
        expect(a.classList.contains('notif-foot-link')).toBe(true);
        const chevron = a.querySelector('span[aria-hidden="true"]')!;
        expect(chevron.textContent).toBe('›');
        expect(a.lastElementChild).toBe(chevron);
        expect(a.textContent?.trim().endsWith('›')).toBe(true);
        expect(a.getAttribute('aria-label')).toBeNull();
      }
    });
  });

  it('phone: rows of 已逾期 are solid, others soft; >= 760 px none is solid', () => {
    const solidIds = (root: HTMLElement) =>
      Array.from(root.querySelectorAll('app-notif-row'))
        .filter(r => r.querySelector('a.notif-primary.solid') !== null)
        .map(r => r.getAttribute('data-task-id'));
    expect(solidIds(mount(390))).toEqual(['a1', 'a2']);
    TestBed.resetTestingModule();
    expect(solidIds(mount(1440))).toEqual([]);
  });

  it('badges off: the page still lists every task, the subtitle and the chip counts stay', () => {
    const root = mount(1440, () => TestBed.inject(NavigationPreferencesService).setShowBadges(false));
    expect(TestBed.inject(NavigationPreferencesService).showBadges()).toBe(false);
    expect(root.querySelectorAll('app-notif-row').length).toBe(5);
    expect(subtitle(root)).toContain('件要處理');
    expect(text(root.querySelector('.notif-chips button'))).toBe('全部5');
  });

  it('is one column: no pane host, no router-outlet', () => {
    const root = mount(1440);
    expect(root.querySelector('router-outlet')).toBeNull();
    expect(root.querySelector('.dbody, .detail-pane, .list-pane')).toBeNull();
  });

  it("a row's run output reaches onRun with its task", () => {
    const root = mount();
    const spy = vi.spyOn(fixture.componentInstance as unknown as { onRun(t: NotifTask, c: unknown): void }, 'onRun');
    const row = fixture.debugElement.queryAll(By.directive(NotifRowComponent))[0];
    const command = { kind: 'postInstance', instanceId: 1, name: '測試' } as const;
    (row.componentInstance as NotifRowComponent).run.emit(command);
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }), command);
    expect(root.querySelectorAll('app-notif-row').length).toBe(5);
  });

  describe('入帳 and 全部入帳 in place', () => {
    // The failed-write paths log with console.error on purpose; keep the run quiet (tests assert on the spy themselves).
    let quiet: { mockRestore(): void };
    beforeEach(() => {
      quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    });
    afterEach(() => quiet.mockRestore());
    const FEE = task('sched:3', 'overdue', {
      from: 'schedules',
      title: '測試月費 · 3 期未入帳',
      primary: { label: '全部入帳', command: { kind: 'catchUp', definitionId: 3, name: '測試月費' } },
    });
    const RENT = task('sched:1', 'today', {
      from: 'schedules',
      title: '測試房租',
      primary: { label: '入帳', command: { kind: 'postInstance', instanceId: 7, name: '測試房租' } },
    });
    const LOAN = task('sched:4', 'today', {
      from: 'schedules',
      title: '測試分期',
      primary: { label: '入帳', command: { kind: 'postInstance', instanceId: 9, name: '測試分期' } },
    });
    /** A 記繳款 row: its primary is a link. */
    const BILL = task('bill:11:2026-10-24', 'today', { title: '測試銀行卡' });
    const ALL = [FEE, RENT, LOAN, BILL];

    let toasts: ToastService;
    const row = (root: HTMLElement, id: string) => root.querySelector<HTMLElement>(`app-notif-row[data-task-id="${id}"]`);
    const control = (root: HTMLElement, id: string) => row(root, id)?.querySelector<HTMLElement>(NOTIF_PRIMARY_CONTROL) ?? null;
    const button = (root: HTMLElement, id: string) => control(root, id) as HTMLButtonElement;
    const press = async (root: HTMLElement, id: string) => {
      button(root, id).click();
      await settle();
    };
    const answer = async <T>(subject: Subject<T>, value: T) => {
      subject.next(value);
      subject.complete();
      await settle();
    };
    const fail = async <T>(subject: Subject<T>, status: number, error: unknown = null) => {
      subject.error(new HttpErrorResponse({ status, error, url: '/api/accounting/schedules/instances/7/post' }));
      await settle();
    };
    const instance = {} as ScheduleInstance;
    const catchUp = (posted: number[], failed: ScheduleCatchUpResult['failed']) =>
      ({ posted, failed, definition: {} }) as unknown as ScheduleCatchUpResult;
    /** The service's next round has settled. */
    const settleRound = async (id: number) => {
      round.set(Math.max(round(), id));
      settledRound.set(id);
      await settle();
    };
    const currentId = () => toasts.current()!.id;

    function open(): HTMLElement {
      tasks.set(ALL);
      const root = mount();
      toasts = TestBed.inject(ToastService);
      return root;
    }

    afterEach(() => fixture?.nativeElement.remove());

    it("入帳 posts the task's instance and shows 「已入帳：測試房租」", async () => {
      const root = open();
      await press(root, RENT.id);
      expect(accounting.postScheduleInstance).toHaveBeenCalledTimes(1);
      expect(accounting.postScheduleInstance).toHaveBeenCalledWith(7);
      expect(accounting.catchUpSchedule).not.toHaveBeenCalled();
      await answer(posts[0], instance);
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳：測試房租', action: null }));
    });

    it('全部入帳 calls catch-up for the definition and shows 「已入帳 3 期：測試月費」', async () => {
      const root = open();
      await press(root, FEE.id);
      expect(accounting.catchUpSchedule).toHaveBeenCalledTimes(1);
      expect(accounting.catchUpSchedule).toHaveBeenCalledWith(3);
      expect(accounting.postScheduleInstance).not.toHaveBeenCalled();
      await answer(catchUps[0], catchUp([31, 32, 33], null));
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳 3 期：測試月費', action: null }));
    });

    it('a catch-up that stops midway shows 失敗 with the count and no reason, without 重試', async () => {
      const root = open();
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([31, 32], { instance_id: 33, error: '帳戶已封存' }));
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試月費：已入帳 2 期，之後的入帳失敗', action: null }),
      );
      // An answer, not a failure: no forced re-read; the row waits for the write's own round.
      expect(invalidate).not.toHaveBeenCalled();
      expect(button(root, FEE.id).disabled).toBe(true);
      await settleRound(2);
      expect(button(root, FEE.id).disabled).toBe(false);
    });

    it('the row is busy while the write runs and a second press sends nothing', async () => {
      const root = open();
      // Two clicks before any change detection: the second reaches onRun while the button is still enabled.
      button(root, RENT.id).click();
      button(root, RENT.id).click();
      await settle();
      expect(accounting.postScheduleInstance).toHaveBeenCalledTimes(1);
      expect(button(root, RENT.id).disabled).toBe(true);
      expect(button(root, RENT.id).getAttribute('aria-busy')).toBe('true');
      const page = fixture.componentInstance as unknown as { onRun(t: NotifTask, c: unknown): void };
      page.onRun(RENT, (RENT.primary as { command: unknown }).command);
      expect(accounting.postScheduleInstance).toHaveBeenCalledTimes(1);
      // Other rows stay pressable.
      expect(button(root, LOAN.id).disabled).toBe(false);
    });

    it('two rows busy at once are independent', async () => {
      round.set(4);
      settledRound.set(4);
      const root = open();
      await press(root, RENT.id);
      await press(root, LOAN.id);
      expect(accounting.postScheduleInstance.mock.calls).toEqual([[7], [9]]);
      await answer(posts[0], instance);
      // RENT waits for round 5; LOAN's write still runs.
      round.set(5);
      await settleRound(5);
      expect(button(root, RENT.id).disabled).toBe(false);
      expect(button(root, LOAN.id).disabled).toBe(true);
      await fail(posts[1], 500);
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(button(root, LOAN.id).disabled).toBe(true);
      expect(button(root, RENT.id).disabled).toBe(false);
      await settleRound(6);
      expect(button(root, LOAN.id).disabled).toBe(false);
    });

    it('after success the row stays busy until a round started after the answer settles', async () => {
      round.set(4);
      settledRound.set(4);
      const root = open();
      await press(root, RENT.id);
      await answer(posts[0], instance);
      // Round 4 is the newest at the answer: it settled before the write landed.
      expect(button(root, RENT.id).disabled).toBe(true);
      round.set(5); // the write's own round (the service's entriesChanged effect)
      await settle();
      expect(button(root, RENT.id).disabled).toBe(true);
      settledRound.set(5);
      await settle();
      expect(button(root, RENT.id).disabled).toBe(false);
      // …or gone, once round 5's list leaves it out.
      tasks.set([FEE, LOAN, BILL]);
      await settle();
      expect(row(root, RENT.id)).toBeNull();
    });

    it('a round that was already in flight when the write answered does not release the row', async () => {
      round.set(4);
      settledRound.set(3);
      const root = open();
      await press(root, RENT.id);
      await answer(posts[0], instance);
      // Round 4 started before the answer and settles late: its reads may predate the write.
      settledRound.set(4);
      await settle();
      expect(button(root, RENT.id).disabled).toBe(true);
      await settleRound(5);
      expect(button(root, RENT.id).disabled).toBe(false);
    });

    it('after a failure the page invalidates and the row stays busy until that round settles', async () => {
      round.set(4);
      settledRound.set(3);
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500);
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(round()).toBe(5);
      expect(button(root, RENT.id).disabled).toBe(true);
      // The superseded round 4 settling (it cannot in the service, but even if it did) does not count.
      settledRound.set(4);
      await settle();
      expect(button(root, RENT.id).disabled).toBe(true);
      settledRound.set(5);
      await settle();
      expect(button(root, RENT.id).disabled).toBe(false);
    });

    it('a failed 入帳 never shows the server text: exact copy, nothing in the DOM, 重試 kept, the error goes to console.error', async () => {
      const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500, { message: 'Internal Server Error: db down' });
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      expect(root.textContent).not.toContain('Internal Server Error');
      expect(root.textContent).not.toContain('db down');
      expect(logged).toHaveBeenCalledTimes(1);
      expect(String(logged.mock.calls[0][0])).toContain(RENT.id);
      expect(logged.mock.calls[0][1]).toBeInstanceOf(HttpErrorResponse);
      await settleRound(2);
      toasts.runAction(currentId());
      expect(accounting.postScheduleInstance.mock.calls).toEqual([[7], [7]]);
      logged.mockRestore();
    });

    it('a failed 入帳 with a reason the app words itself keeps it: 409 skipped, no 「請再試一次」', async () => {
      const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 409, { code: 'conflict', message: 'skipped' });
      expect(toasts.current()?.text).toBe('測試房租 入帳失敗：此期已略過');
      expect(root.textContent).not.toContain('請再試一次');
      expect(logged.mock.calls[0][1]).toBeInstanceOf(HttpErrorResponse);
      logged.mockRestore();
    });

    it('a 全部入帳 that stops with a conflict code names the reason; an unknown string stays out', async () => {
      const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      const root = open();
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([31], { instance_id: 32, error: 'import_running' }));
      expect(toasts.current()?.text).toBe(`測試月費：已入帳 1 期，之後的入帳失敗（${IMPORT_RUNNING_TOAST}）`);
      expect(logged.mock.calls.flat().map(String).join(' ')).toContain('import_running');
      logged.mockRestore();
    });

    it('a 全部入帳 that stops midway logs the reason to console.error and keeps it out of the toast', async () => {
      const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      const root = open();
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([31], { instance_id: 32, error: 'ledger locked: db 7' }));
      expect(toasts.current()?.text).toBe('測試月費：已入帳 1 期，之後的入帳失敗');
      expect(logged.mock.calls.flat().map(String).join(' ')).toContain('ledger locked: db 7');
      logged.mockRestore();
    });

    it('a 500 shows 失敗 with 重試, which posts the same instance again', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500);
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      await settleRound(2);
      toasts.runAction(currentId());
      await settle();
      expect(accounting.postScheduleInstance.mock.calls).toEqual([[7], [7]]);
      expect(button(root, RENT.id).disabled).toBe(true);
    });

    it('a network failure (status 0) also offers 重試', async () => {
      const root = open();
      await press(root, FEE.id);
      await fail(catchUps[0], 0);
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試月費 入帳失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      await settleRound(2);
      toasts.runAction(currentId());
      expect(accounting.catchUpSchedule.mock.calls).toEqual([[3], [3]]);
    });

    it('重試 pressed while that task is busy sends nothing and shows the toast again', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500);
      const first = currentId();
      // The re-read (round 2) has not settled: the row is still busy.
      toasts.runAction(first);
      await settle();
      expect(accounting.postScheduleInstance).toHaveBeenCalledTimes(1);
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      expect(currentId()).not.toBe(first);
      expect(button(root, RENT.id).disabled).toBe(true);
      // Once it settles the re-raised toast's 重試 sends.
      await settleRound(2);
      toasts.runAction(currentId());
      expect(accounting.postScheduleInstance.mock.calls).toEqual([[7], [7]]);
    });

    it('重試 still sends after the row left the list', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500);
      tasks.set([]);
      await settleRound(2);
      expect(row(root, RENT.id)).toBeNull();
      toasts.runAction(currentId());
      expect(accounting.postScheduleInstance.mock.calls).toEqual([[7], [7]]);
    });

    it('a 409 import_running shows 「匯入進行中，請稍後再試」 without 重試', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 409, { code: 'CONFLICT', message: 'import_running' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: IMPORT_RUNNING_TOAST, action: null }));
      expect(IMPORT_RUNNING_TOAST).toBe('匯入進行中，請稍後再試');
      expect(invalidate).toHaveBeenCalledTimes(1);
    });

    it('a 422 shows the generic copy, not the server message, without 重試', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 422, { code: 'VALIDATION', message: '帳戶已封存' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: null }));
    });

    it('a 409 already_posted reads as done: 「測試房租：此期已入帳」, no 重試, a re-read, released by the newer round', async () => {
      round.set(4);
      settledRound.set(3);
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 409, { detail: 'already_posted' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試房租：此期已入帳', action: null }));
      // An error bumps nothing: the page re-reads itself.
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(round()).toBe(5);
      expect(button(root, RENT.id).disabled).toBe(true);
      settledRound.set(4);
      await settle();
      expect(button(root, RENT.id).disabled).toBe(true);
      settledRound.set(5);
      await settle();
      expect(button(root, RENT.id).disabled).toBe(false);
    });

    it('already_posted in the shared_lib body (`message`) reads as done too; another 409 code stays a failure', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 409, { code: 'CONFLICT', message: 'already_posted' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試房租：此期已入帳', action: null }));
      await press(root, LOAN.id);
      await fail(posts[1], 409, { detail: 'skipped' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試分期 入帳失敗：此期已略過', action: null }));
    });

    it('a 全部入帳 that posted nothing reads 「測試月費：沒有待入帳的期數」 and waits for the write\'s own round', async () => {
      round.set(4);
      settledRound.set(4);
      const root = open();
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([], null));
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試月費：沒有待入帳的期數', action: null }));
      // The call bumps entriesChanged on any answer: the service starts the round, the page does not.
      expect(invalidate).not.toHaveBeenCalled();
      expect(button(root, FEE.id).disabled).toBe(true);
      await settleRound(5);
      expect(button(root, FEE.id).disabled).toBe(false);
    });

    it('a 全部入帳 that posted nothing and then failed keeps the failure copy', async () => {
      const root = open();
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([], { instance_id: 31, error: '帳戶已封存' }));
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試月費：已入帳 0 期，之後的入帳失敗', action: null }),
      );
    });

    it('focus follows an already_posted row that leaves, as for a success', async () => {
      const root = open();
      document.body.appendChild(root);
      control(root, RENT.id)!.focus();
      await press(root, RENT.id);
      await fail(posts[0], 409, { detail: 'already_posted' });
      tasks.set([FEE, LOAN, BILL]);
      await settleRound(2);
      await settle();
      expect(document.activeElement).toBe(control(root, LOAN.id));
    });

    it('a failed write reloads silently: the 失敗 toast with 重試 is still current after the newer round lands', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500);
      const id = currentId();
      expect(invalidate).toHaveBeenCalledTimes(1);
      tasks.set([FEE, LOAN, BILL]);
      await settleRound(invalidate.mock.results[0].value as number);
      expect(currentId()).toBe(id);
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', action: expect.objectContaining({ label: '重試' }) }));
      expect(row(root, RENT.id)).toBeNull();
    });

    it('success calls neither refresh nor invalidate', async () => {
      const root = open();
      expect(refresh).toHaveBeenCalledTimes(1); // page open
      await press(root, RENT.id);
      await answer(posts[0], instance);
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([31], null));
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(invalidate).not.toHaveBeenCalled();
    });

    it('a result landing after the page is destroyed still shows, without an action', async () => {
      const root = open();
      await press(root, RENT.id);
      fixture.destroy();
      expect(() => posts[0].error(new HttpErrorResponse({ status: 500, error: null }))).not.toThrow();
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: null }),
      );
      // The re-read still starts: the write may have committed.
      expect(invalidate).toHaveBeenCalledTimes(1);
    });

    it('a success landing after the page is destroyed still shows its done toast', async () => {
      const root = open();
      await press(root, FEE.id);
      fixture.destroy();
      expect(() => {
        catchUps[0].next(catchUp([31, 32, 33], null));
        catchUps[0].complete();
      }).not.toThrow();
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳 3 期：測試月費', action: null }));
    });

    it('a 記繳款 row runs no command', async () => {
      const root = open();
      expect(row(root, BILL.id)!.querySelector('app-btn')).toBeNull();
      const link = control(root, BILL.id)!;
      expect(link.tagName).toBe('A');
      // A modified click: the router leaves it to the browser, so the test does not navigate.
      link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true }));
      await settle();
      expect(accounting.postScheduleInstance).not.toHaveBeenCalled();
      expect(accounting.catchUpSchedule).not.toHaveBeenCalled();
    });

    describe('focus', () => {
      /** Presses the 入帳 row `id` with focus on its control and answers ok; then round 2 lists `after` and settles. */
      async function postAndLeave(root: HTMLElement, id: string, after: NotifTask[], subject: () => Subject<ScheduleInstance>): Promise<void> {
        control(root, id)!.focus();
        expect(document.activeElement).toBe(control(root, id));
        await press(root, id);
        await answer(subject(), instance);
        tasks.set(after);
        await settleRound(round() + 1);
        await settle();
      }

      it("when the posted row leaves and focus fell to <body>, the next row's native control has focus", async () => {
        const root = open();
        document.body.appendChild(root);
        await postAndLeave(root, RENT.id, [FEE, LOAN, BILL], () => posts[0]);
        expect(row(root, RENT.id)).toBeNull();
        expect(document.activeElement).toBe(control(root, LOAN.id));
        expect(document.activeElement?.tagName).toBe('BUTTON');

        await postAndLeave(root, LOAN.id, [FEE, BILL], () => posts[1]);
        expect(document.activeElement).toBe(control(root, BILL.id));
        expect(document.activeElement?.tagName).toBe('A');
      });

      it('a next row that is busy is skipped for the next enabled control', async () => {
        const root = open();
        document.body.appendChild(root);
        await press(root, LOAN.id); // still running
        await postAndLeave(root, RENT.id, [FEE, LOAN, BILL], () => posts[1]);
        expect(button(root, LOAN.id).disabled).toBe(true);
        expect(document.activeElement).toBe(root.querySelector('details.notif-tracking > summary'));
      });

      it('with no row left it goes to the 追蹤中 summary, else the first chip', async () => {
        const root = open();
        document.body.appendChild(root);
        tasks.set([RENT]);
        await settle();
        await postAndLeave(root, RENT.id, [], () => posts[0]);
        expect(document.activeElement).toBe(root.querySelector('details.notif-tracking > summary'));

        trackingRows.set([]);
        tasks.set([LOAN]);
        await settle();
        await postAndLeave(root, LOAN.id, [], () => posts[1]);
        expect(document.activeElement).toBe(root.querySelector('.notif-chips button'));
      });

      it('focus the owner moved elsewhere is left alone', async () => {
        const root = open();
        document.body.appendChild(root);
        control(root, RENT.id)!.focus();
        await press(root, RENT.id);
        await answer(posts[0], instance);
        const elsewhere = root.querySelector<HTMLElement>('a.notif-reminders')!;
        elsewhere.focus();
        tasks.set([FEE, LOAN, BILL]);
        await settleRound(2);
        await settle();
        expect(document.activeElement).toBe(elsewhere);
      });

      it('a failed write that in fact committed: when the row leaves, focus goes to the next row too (P6)', async () => {
        const root = open();
        document.body.appendChild(root);
        control(root, RENT.id)!.focus();
        await press(root, RENT.id);
        await fail(posts[0], 500);
        tasks.set([FEE, LOAN, BILL]);
        await settleRound(2);
        await settle();
        expect(document.activeElement).toBe(control(root, LOAN.id));
      });

      /**
       * jsdom keeps focus on a button that becomes disabled (and ignores blur() on a disabled one); Chromium drops it
       * to <body>. Move focus to a throwaway element and remove it to reproduce the browser.
       */
      const browserDropsFocus = () => {
        const tmp = document.createElement('button');
        document.body.append(tmp);
        tmp.focus();
        tmp.remove();
      };

      it('a row that stays after a failed write gets focus back on its control when the browser dropped it', async () => {
        const root = open();
        document.body.appendChild(root);
        control(root, RENT.id)!.focus();
        await press(root, RENT.id);
        browserDropsFocus();
        expect(document.activeElement).toBe(document.body);
        await fail(posts[0], 500);
        await settleRound(2);
        await settle();
        expect(button(root, RENT.id).disabled).toBe(false);
        expect(document.activeElement).toBe(control(root, RENT.id));
      });

      it('a catch-up with a partial failure whose row stays: its control gets focus back when the browser dropped it', async () => {
        const root = open();
        document.body.appendChild(root);
        control(root, FEE.id)!.focus();
        await press(root, FEE.id);
        browserDropsFocus();
        await answer(catchUps[0], catchUp([31, 32], { instance_id: 33, error: '帳戶已封存' }));
        await settleRound(2);
        await settle();
        expect(button(root, FEE.id).disabled).toBe(false);
        expect(document.activeElement).toBe(control(root, FEE.id));
      });

      it('focus that was not on the row when the command was sent is not moved to it', async () => {
        const root = open();
        document.body.appendChild(root);
        await press(root, RENT.id);
        await fail(posts[0], 500);
        await settleRound(2);
        await settle();
        expect(document.activeElement).toBe(document.body);
      });

      it('a row that stays does not take focus the owner moved elsewhere meanwhile', async () => {
        const root = open();
        document.body.appendChild(root);
        control(root, RENT.id)!.focus();
        await press(root, RENT.id);
        browserDropsFocus();
        const elsewhere = root.querySelector<HTMLElement>('a.notif-reminders')!;
        elsewhere.focus();
        await fail(posts[0], 500);
        await settleRound(2);
        await settle();
        expect(document.activeElement).toBe(elsewhere);
      });

      it('a row that stays (the write did not commit) keeps focus on its own control', async () => {
        const root = open();
        document.body.appendChild(root);
        control(root, RENT.id)!.focus();
        await press(root, RENT.id);
        await fail(posts[0], 500);
        await settleRound(2);
        await settle();
        expect(button(root, RENT.id).disabled).toBe(false);
        expect(document.activeElement).toBe(control(root, RENT.id));
      });
    });
  });

  describe('投資: 已處理 for a 國泰 date, tracking rows by source, the 國泰 note', () => {
    let logged: { mock: { calls: unknown[][] }; mockRestore(): void };
    beforeEach(() => {
      logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    });
    afterEach(() => {
      logged.mockRestore();
      fixture?.nativeElement.remove();
    });

    const CATHAY = task('cathay:2026-10-28', 'system', {
      source: 'portfolio',
      from: 'cathay',
      icon: 'pi-file',
      title: '國泰對帳單 10/28',
      when: '保留',
      meta: '自 10/28 起',
      amount: null,
      primary: { label: '已處理', command: { kind: 'ackCathay', tradeDate: '2026-10-28', name: '國泰對帳單 10/28' } },
      secondary: [{ label: '查看', route: '/portfolio/import', fragment: 'cathay' }],
      sortKey: '2026-10-28',
    });
    const DIV = task('div:batch', 'confirm', {
      source: 'portfolio',
      from: 'dividends',
      icon: 'pi-percentage',
      title: '股利待確認收款 · 2 筆',
      when: '最早 10/20 發放',
      meta: '9901、9902',
      amount: null,
      primary: { label: '查看', route: '/portfolio/dividends', queryParams: { status: 'pending' } },
    });
    const IMPORT = task('import:fail:9', 'system', {
      source: 'portfolio',
      from: 'imports',
      icon: 'pi-upload',
      title: '最近一次匯入失敗',
      when: '10/27 21:05',
      meta: '測試匯入.csv',
      amount: null,
      primary: { label: '查看', route: '/portfolio/import' },
    });
    const MIXED = [task('a1', 'overdue'), task('b1', 'today'), DIV, IMPORT, CATHAY];
    const DIV_ROW: NotifTrackingRow = {
      id: 'dividends',
      source: 'portfolio',
      title: '股利待發放 · 3 筆',
      meta: '9901 11/05 · 9902 11/12 · 9903 日期未定',
      count: 3,
      link: { label: '查看', route: '/portfolio/dividends', queryParams: { status: 'pending' } },
    };

    let toasts: ToastService;
    const q = (root: HTMLElement, sel: string) => root.querySelector<HTMLElement>(sel);
    const chips = (root: HTMLElement) => Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button'));
    const ids = (root: HTMLElement) => Array.from(root.querySelectorAll('app-notif-row')).map(r => r.getAttribute('data-task-id'));
    const ack = (root: HTMLElement) =>
      q(root, `app-notif-row[data-task-id="${CATHAY.id}"]`)!.querySelector<HTMLButtonElement>(NOTIF_PRIMARY_CONTROL)!;
    const answer = async () => {
      acks[0].next({ trade_date: '2026-10-28', ack_status: 'held' });
      acks[0].complete();
      await settle();
    };
    const fail = async (status: number, error: unknown = null, index = 0) => {
      acks[index].error(new HttpErrorResponse({ status, error, url: '/api/portfolio/imports/cathay-statement/days/2026-10-28/ack' }));
      await settle();
    };
    const settleRound = async (id: number) => {
      round.set(Math.max(round(), id));
      settledRound.set(id);
      await settle();
    };

    function open(list: readonly NotifTask[] = MIXED): HTMLElement {
      tasks.set(list);
      const root = mount();
      document.body.appendChild(root);
      toasts = TestBed.inject(ToastService);
      return root;
    }

    it('投資 chip: only the dividend, import and 國泰 tasks remain, and the chip reads 投資，3 件', async () => {
      const root = open();
      expect(chips(root)[2].getAttribute('aria-label')).toBe('投資，3 件');
      chips(root)[2].click();
      await settle();
      expect(ids(root)).toEqual(['div:batch', 'import:fail:9', 'cathay:2026-10-28']);
      expect(chips(root)[0].getAttribute('aria-label')).toBe('全部，5 件');
    });

    it('a 系統 section holds the 國泰 rows with 已處理 as a button named 已處理：國泰對帳單 10/28 and 查看 as a link to the card (#cathay)', () => {
      const root = open();
      const system = Array.from(root.querySelectorAll('section.notif-section')).find(s => text(s.querySelector('h2')).startsWith('系統'))!;
      expect(text(system.querySelector('h2'))).toBe('系統 2');
      const row = system.querySelector(`app-notif-row[data-task-id="${CATHAY.id}"]`)!;
      const button = row.querySelector(NOTIF_PRIMARY_CONTROL)!;
      expect(button.tagName).toBe('BUTTON');
      expect(text(button)).toBe('已處理');
      expect(button.getAttribute('aria-label')).toBe('已處理：國泰對帳單 10/28');
      const look = row.querySelector('a.notif-secondary')!;
      expect(text(look)).toBe('查看');
      expect(look.getAttribute('href')).toBe('/portfolio/import#cathay');
    });

    it('已處理 calls ackCathayDay("2026-10-28") once, the row is busy, then 已處理：國泰對帳單 10/28 and one invalidate()', async () => {
      round.set(4);
      settledRound.set(4);
      const root = open();
      ack(root).click();
      await settle();
      expect(portfolio.ackCathayDay).toHaveBeenCalledTimes(1);
      expect(portfolio.ackCathayDay).toHaveBeenCalledWith('2026-10-28');
      expect(accounting.postScheduleInstance).not.toHaveBeenCalled();
      expect(accounting.catchUpSchedule).not.toHaveBeenCalled();
      expect(ack(root).disabled).toBe(true);
      expect(invalidate).not.toHaveBeenCalled();
      await answer();
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已處理：國泰對帳單 10/28', action: null }));
      // An ack bumps no change signal: the page re-reads itself, once.
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledTimes(1); // the open only
      expect(round()).toBe(5);
      expect(ack(root).disabled).toBe(true);
      // The round read before the invalidate settling does not release the row; the re-read does.
      settledRound.set(4);
      await settle();
      expect(ack(root).disabled).toBe(true);
      await settleRound(5);
      expect(ack(root).disabled).toBe(false);
      // …and once that round's list leaves the date out, the row is gone.
      tasks.set(MIXED.filter(t => t.id !== CATHAY.id));
      await settle();
      expect(q(root, `app-notif-row[data-task-id="${CATHAY.id}"]`)).toBeNull();
    });

    it('a second press while busy sends nothing', async () => {
      const root = open();
      ack(root).click();
      ack(root).click();
      await settle();
      expect(portfolio.ackCathayDay).toHaveBeenCalledTimes(1);
      const page = fixture.componentInstance as unknown as { onRun(t: NotifTask, c: unknown): void };
      page.onRun(CATHAY, (CATHAY.primary as { command: unknown }).command);
      expect(portfolio.ackCathayDay).toHaveBeenCalledTimes(1);
    });

    it('404 reads as done: 國泰對帳單 10/28：已經不需要處理, no 重試, invalidate() called', async () => {
      round.set(4);
      settledRound.set(3);
      const root = open();
      ack(root).click();
      await settle();
      await fail(404, { detail: 'Not Found' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '國泰對帳單 10/28：已經不需要處理', action: null }));
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(round()).toBe(5);
      expect(logged.mock.calls.length).toBe(0);
      expect(ack(root).disabled).toBe(true);
      settledRound.set(4);
      await settle();
      expect(ack(root).disabled).toBe(true);
      await settleRound(5);
      expect(ack(root).disabled).toBe(false);
    });

    it('500: 失敗 toast 國泰對帳單 10/28 標記失敗 with 重試; the raw body goes to console.error, not the toast', async () => {
      const root = open();
      ack(root).click();
      await settle();
      await fail(500, { message: 'Internal Server Error: db down' });
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 標記失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      expect(root.textContent).not.toContain('db down');
      expect(document.body.textContent).not.toContain('db down');
      expect(logged.mock.calls.length).toBe(1);
      expect(String(logged.mock.calls[0][0])).toContain(CATHAY.id);
      expect(logged.mock.calls[0][1]).toBeInstanceOf(HttpErrorResponse);
      // An error bumps nothing, and the ack may have committed: the page re-reads.
      expect(invalidate).toHaveBeenCalledTimes(1);
      expect(ack(root).disabled).toBe(true);
    });

    it('403: 失敗 toast without 重試', async () => {
      const root = open();
      ack(root).click();
      await settle();
      await fail(403, { detail: 'write scope required' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 標記失敗', action: null }));
      expect(root.textContent).not.toContain('write scope');
      expect(invalidate).toHaveBeenCalledTimes(1);
    });

    it('a reason the app words itself is appended: 409 import_running', async () => {
      const root = open();
      ack(root).click();
      await settle();
      await fail(409, { code: 'CONFLICT', message: 'import_running' });
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: `國泰對帳單 10/28 標記失敗：${IMPORT_RUNNING_TOAST}`, action: null }),
      );
    });

    it('重試 re-sends the captured date; while the row is busy it re-raises the toast instead', async () => {
      const root = open();
      ack(root).click();
      await settle();
      await fail(500);
      const first = toasts.current()!.id;
      toasts.runAction(first);
      await settle();
      expect(portfolio.ackCathayDay).toHaveBeenCalledTimes(1);
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 標記失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      expect(toasts.current()!.id).not.toBe(first);
      await settleRound(2);
      expect(ack(root).disabled).toBe(false);
      // The row may have left by now (the ack had committed): 重試 still sends the captured date.
      tasks.set(MIXED.filter(t => t.id !== CATHAY.id));
      await settle();
      toasts.runAction(toasts.current()!.id);
      await settle();
      expect(portfolio.ackCathayDay.mock.calls).toEqual([['2026-10-28'], ['2026-10-28']]);
      await fail(404, null, 1);
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '國泰對帳單 10/28：已經不需要處理', action: null }));
    });

    it('the dividends tracking row shows under 全部 and 投資, the ledger rows under 全部 and 財務', async () => {
      trackingRows.set([...ROWS, DIV_ROW]);
      const root = open();
      const titles = () => Array.from(root.querySelectorAll('.tracking-title')).map(t => text(t));
      expect(titles()).toEqual(['借還款 · 3 位', '即將到來 · 2 件', '股利待發放 · 3 筆']);
      expect(text(q(root, '.tracking-sum'))).toBe('借還款 3 位 · 即將到來 2 件 · 股利 3 筆');
      chips(root)[2].click();
      await settle();
      expect(titles()).toEqual(['股利待發放 · 3 筆']);
      expect(text(q(root, '.tracking-sum'))).toBe('股利 3 筆');
      expect(q(root, 'a.tracking-link')?.getAttribute('href')).toBe('/portfolio/dividends?status=pending');
      chips(root)[1].click();
      await settle();
      expect(titles()).toEqual(['借還款 · 3 位', '即將到來 · 2 件']);
      expect(text(q(root, '.tracking-sum'))).toBe('借還款 3 位 · 即將到來 2 件');
    });

    it("the all-clear's 下一件 stays ledger-only; under 投資 its tracking link counts the dividends row", async () => {
      trackingRows.set([...ROWS, DIV_ROW]);
      nextUpcoming.set(task('bill:11:2026-10-27', 'tracking', { title: '測試銀行卡帳單', when: '10/27 截止', amount: '$1,824' }));
      const root = open([]);
      expect(text(q(root, '.empty-next'))).toBe('下一件：測試銀行卡帳單 · 10/27 截止 · $1,824');
      expect(text(q(root, '.empty-tracking'))).toBe('看追蹤中的 8 件 ›');
      chips(root)[2].click();
      await settle();
      expect(q(root, '.notif-empty h2')).not.toBeNull();
      expect(q(root, '.empty-next')).toBeNull();
      expect(text(q(root, '.empty-tracking'))).toBe('看追蹤中的 3 件 ›');
    });

    it('cathayMore 23: 還有 23 天未列出，處理完上面的會接著顯示 (no link) under 全部 and 投資, not under 財務, and no count includes it', async () => {
      cathayMore.set(23);
      const root = open();
      const note = () => q(root, 'p.notif-overflow.notif-overflow--portfolio');
      expect(text(note())).toBe('還有 23 天未列出，處理完上面的會接著顯示');
      expect(note()!.querySelector('a')).toBeNull();
      expect(note()!.closest('section')).toBeNull();
      expect(note()!.previousElementSibling?.matches('section.notif-section')).toBe(true);
      // Never counted: chips, sections and subtitle are the rows' numbers.
      expect(chips(root).map(c => c.getAttribute('aria-label'))).toEqual(['全部，5 件', '財務，2 件', '投資，3 件']);
      expect(subtitle(root)).toContain('5 件要處理');
      expect(root.querySelectorAll('app-notif-row').length).toBe(5);
      chips(root)[2].click();
      await settle();
      expect(text(note())).toBe('還有 23 天未列出，處理完上面的會接著顯示');
      chips(root)[1].click();
      await settle();
      expect(note()).toBeNull();
    });

    it('the 國泰 note comes after the ledger overflow note', () => {
      cathayMore.set(23);
      truncated.set(true);
      overflow.set(task('recon:more', 'confirm', { from: 'recon', title: '還有 12 筆對帳項目未列出', when: '', primary: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'todo' } } }));
      const root = open();
      const notes = Array.from(root.querySelectorAll('p.notif-overflow'));
      expect(notes.map(n => text(n))).toEqual(['還有 12 筆對帳項目未列出 · 到提醒中心查看 ›', '還有 23 天未列出，處理完上面的會接著顯示']);
    });

    it('cathayMore 0 or the page still pending: no portfolio note', async () => {
      const root = open();
      expect(q(root, '.notif-overflow--portfolio')).toBeNull();
      root.remove();
      TestBed.resetTestingModule();
      cathayMore.set(23);
      loaded.set(false);
      const pendingRoot = open([]);
      expect(q(pendingRoot, '.notif-overflow--portfolio')).toBeNull();
      loaded.set(true);
      await settle();
      expect(text(q(pendingRoot, '.notif-overflow--portfolio'))).toBe('還有 23 天未列出，處理完上面的會接著顯示');
    });
  });

  describe('the review flow (/notifications/recon/:statementId)', () => {
    const RECON = task('recon:12', 'confirm', {
      from: 'recon',
      title: '對帳 · 測試銀行卡（至 10/05）',
      primary: { label: '對帳', route: '/notifications/recon/12' },
    });
    const RECON_OTHER = task('recon:13', 'confirm', {
      from: 'recon',
      title: '對帳 · 測試信用卡（至 10/08）',
      primary: { label: '對帳', route: '/notifications/recon/13' },
    });
    /** The flow's reads never answer here: it stays on its 讀取中… step. */
    const inbox = { get: vi.fn(() => NEVER), apply: vi.fn(() => NEVER) };

    let harness: RouterTestingHarness;
    let router: Router;
    const root = () => harness.fixture.nativeElement as HTMLElement;
    const q = (sel: string) => root().querySelector<HTMLElement>(sel);
    const flow = () => q('app-recon-flow');
    const rowControl = (id: string) => rowPrimary(root(), id);
    const settleFlow = async () => {
      await harness.fixture.whenStable();
      harness.detectChanges();
      await harness.fixture.whenStable();
    };

    async function openAt(mode: LayoutMode, url: string): Promise<void> {
      TestBed.configureTestingModule({
        providers: [
          provideRouter([{ matcher: notificationsMatcher, component: NotificationsComponent }]),
          { provide: NotificationCenterService, useValue: stub },
          { provide: AccountingService, useValue: accounting },
          { provide: PortfolioService, useValue: portfolio },
          { provide: InboxService, useValue: inbox },
        ],
      });
      TestBed.inject(LayoutModeService).set(mode);
      tasks.set([...TASKS, RECON, RECON_OTHER]);
      router = TestBed.inject(Router);
      harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      await settleFlow();
    }

    afterEach(() => harness?.fixture.destroy());

    it('at panes width: the list and the flow side by side, the flow in a pane of its own; one h1', async () => {
      await openAt('panes', '/notifications/recon/12');
      const split = q('.notif-split')!;
      expect(split.classList.contains('notif-split--open')).toBe(true);
      expect(split.querySelector(':scope > .notif-page')).not.toBeNull();
      const pane = split.querySelector<HTMLElement>(':scope > .notif-pane')!;
      expect(pane.querySelector('app-recon-flow')).not.toBeNull();
      expect(root().querySelectorAll('app-notif-row').length).toBe(7);
      expect(q('.notif-chips')).not.toBeNull();
      expect(text(q('h1'))).toBe('通知');
      expect(root().querySelectorAll('h1').length).toBe(1);
      expect(inbox.get).toHaveBeenCalled();
    });

    it('at panes width the row of the open statement is selected: 對帳中, aria-current; the others are not', async () => {
      await openAt('panes', '/notifications/recon/12');
      const selected = Array.from(root().querySelectorAll('app-notif-row.notif-row--selected')).map(r => r.getAttribute('data-task-id'));
      expect(selected).toEqual(['recon:12']);
      expect(text(rowControl('recon:12'))).toBe('對帳中');
      expect(rowControl('recon:12')!.getAttribute('aria-current')).toBe('true');
      expect(text(rowControl('recon:13'))).toBe('對帳');
      expect(rowControl('recon:13')!.hasAttribute('aria-current')).toBe(false);
    });

    it('opening the flow moves focus to the flow host', async () => {
      await openAt('panes', '/notifications');
      expect(flow()).toBeNull();
      rowControl('recon:12')!.focus();
      rowControl('recon:12')!.click();
      await vi.waitFor(async () => {
        await settleFlow();
        expect(router.url).toBe('/notifications/recon/12');
        expect(flow()).not.toBeNull();
        expect(document.activeElement).toBe(flow());
      });
    });

    it('a direct load with an id focuses the flow host once it is rendered', async () => {
      await openAt('panes', '/notifications/recon/12');
      await vi.waitFor(() => expect(document.activeElement).toBe(flow()));
    });

    for (const mode of ['phone', 'sheet'] as const) {
      it(`below panes (${mode}): only the flow renders: no page header, chips, list or links`, async () => {
        await openAt(mode, '/notifications/recon/12');
        expect(flow()).not.toBeNull();
        expect(q('app-page-header')).toBeNull();
        expect(q('.notif-chips')).toBeNull();
        expect(root().querySelectorAll('app-notif-row').length).toBe(0);
        expect(q('a.notif-reminders')).toBeNull();
        expect(q('.notif-page')).toBeNull();
        // No card around the flow (it draws its own) and no split row.
        expect(q('.notif-pane')!.classList.contains('notif-pane--card')).toBe(false);
        expect(q('.notif-split')!.classList.contains('notif-split--open')).toBe(false);
      });
    }

    it('crossing 1024 px with the flow open keeps the same flow (node and component): no remount', async () => {
      await openAt('panes', '/notifications/recon/12');
      const layout = TestBed.inject(LayoutModeService);
      const node = flow();
      const instance = harness.fixture.debugElement.query(By.directive(ReconFlowComponent)).componentInstance;
      expect(q('.notif-pane')!.classList.contains('notif-pane--card')).toBe(true);
      layout.set('sheet');
      await settleFlow();
      expect(flow()).toBe(node);
      expect(harness.fixture.debugElement.query(By.directive(ReconFlowComponent)).componentInstance).toBe(instance);
      expect(q('.notif-page')).toBeNull();
      expect(q('.notif-pane')!.classList.contains('notif-pane--card')).toBe(false);
      layout.set('panes');
      await settleFlow();
      expect(flow()).toBe(node);
      expect(harness.fixture.debugElement.query(By.directive(ReconFlowComponent)).componentInstance).toBe(instance);
      expect(q('.notif-page')).not.toBeNull();
      expect(q('.notif-pane')!.classList.contains('notif-pane--card')).toBe(true);
    });

    describe('scrolling below panes (jsdom has no element scrolling: stubbed)', () => {
      let scrollTo: ReturnType<typeof vi.fn>;
      let scrollIntoView: ReturnType<typeof vi.fn>;
      const proto = Element.prototype as unknown as Record<string, unknown>;
      const saved = { scrollTo: proto['scrollTo'], scrollIntoView: proto['scrollIntoView'] };
      beforeEach(() => {
        scrollTo = vi.fn();
        scrollIntoView = vi.fn();
        proto['scrollTo'] = scrollTo;
        proto['scrollIntoView'] = scrollIntoView;
      });
      afterEach(() => {
        proto['scrollTo'] = saved.scrollTo;
        proto['scrollIntoView'] = saved.scrollIntoView;
      });
      const pageScroller = () => document.scrollingElement ?? document.documentElement;

      it('opening the flow alone scrolls the page to the top', async () => {
        await openAt('phone', '/notifications');
        expect(scrollTo).not.toHaveBeenCalled();
        rowControl('recon:12')!.click();
        await vi.waitFor(async () => {
          await settleFlow();
          expect(flow()).not.toBeNull();
          expect(scrollTo.mock.contexts).toContain(pageScroller());
        });
        expect(scrollTo).toHaveBeenCalledWith({ top: 0 });
      });

      it('opening the flow beside the list (panes) does not scroll the page', async () => {
        await openAt('panes', '/notifications');
        rowControl('recon:12')!.click();
        await vi.waitFor(async () => {
          await settleFlow();
          expect(document.activeElement).toBe(flow());
        });
        expect(scrollTo).not.toHaveBeenCalled();
      });

      it('closing scrolls the focused row into view', async () => {
        await openAt('phone', '/notifications/recon/12');
        (flow()!.querySelector('button.flow-back') as HTMLButtonElement).click();
        await vi.waitFor(async () => {
          await settleFlow();
          expect(document.activeElement).toBe(rowControl('recon:12'));
        });
        expect(scrollIntoView.mock.contexts).toContain(rowControl('recon:12'));
        expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
      });
    });

    it('without an id there is no flow and the page is as before', async () => {
      await openAt('panes', '/notifications');
      expect(flow()).toBeNull();
      expect(q('.notif-pane')).toBeNull();
      expect(q('.notif-split')!.classList.contains('notif-split--open')).toBe(false);
      expect(root().querySelectorAll('app-notif-row').length).toBe(7);
      expect(root().querySelector('app-notif-row.notif-row--selected')).toBeNull();
    });

    it('a statement id of 0 or with a leading zero is not routed: the list stays, no flow', async () => {
      await openAt('phone', '/notifications');
      for (const url of ['/notifications/recon/0', '/notifications/recon/012']) {
        await expect(router.navigateByUrl(url)).rejects.toThrow();
        await settleFlow();
        expect(router.url).toBe('/notifications');
        expect(flow()).toBeNull();
        expect(root().querySelectorAll('app-notif-row').length).toBe(7);
      }
    });

    it('closing at panes width navigates to /notifications and focuses the row of that statement', async () => {
      await openAt('panes', '/notifications/recon/12');
      (flow()!.querySelector('button.flow-close') as HTMLButtonElement).click();
      await vi.waitFor(async () => {
        await settleFlow();
        expect(router.url).toBe('/notifications');
        expect(flow()).toBeNull();
        expect(document.activeElement).toBe(rowControl('recon:12'));
      });
      expect(text(rowControl('recon:12'))).toBe('對帳');
    });

    it('closing replaces the flow\'s history entry: browser Back does not reopen it', async () => {
      await openAt('panes', '/notifications/recon/12');
      const navigate = vi.spyOn(router, 'navigate');
      (flow()!.querySelector('button.flow-close') as HTMLButtonElement).click();
      await vi.waitFor(async () => {
        await settleFlow();
        expect(router.url).toBe('/notifications');
      });
      expect(navigate).toHaveBeenCalledWith(['/notifications'], { replaceUrl: true });
    });

    it('closing when the row has left the list focuses the page heading', async () => {
      await openAt('panes', '/notifications/recon/12');
      tasks.set(TASKS);
      await settleFlow();
      (flow()!.querySelector('button.flow-close') as HTMLButtonElement).click();
      await vi.waitFor(async () => {
        await settleFlow();
        expect(router.url).toBe('/notifications');
        expect(document.activeElement).toBe(q('h1'));
      });
      // Focusable only for this hand-off: the tabindex leaves with the focus.
      expect(q('h1')!.getAttribute('tabindex')).toBe('-1');
      q('h1')!.blur();
      expect(q('h1')!.hasAttribute('tabindex')).toBe(false);
    });

    it('closing below panes (the flow\'s back button) brings the list back and focuses the row of that statement', async () => {
      await openAt('phone', '/notifications/recon/12');
      (flow()!.querySelector('button.flow-back') as HTMLButtonElement).click();
      await vi.waitFor(async () => {
        await settleFlow();
        expect(router.url).toBe('/notifications');
        expect(flow()).toBeNull();
        expect(root().querySelectorAll('app-notif-row').length).toBe(7);
        expect(document.activeElement).toBe(rowControl('recon:12'));
      });
    });

    it('another statement\'s 對帳 while the flow is open changes the statement in the same flow', async () => {
      await openAt('panes', '/notifications/recon/12');
      const before = flow();
      const page = q('.notif-page');
      rowControl('recon:13')!.click();
      await vi.waitFor(async () => {
        await settleFlow();
        expect(router.url).toBe('/notifications/recon/13');
        expect(text(rowControl('recon:13'))).toBe('對帳中');
      });
      expect(flow()).toBe(before);
      expect(q('.notif-page')).toBe(page);
      expect(text(rowControl('recon:12'))).toBe('對帳');
    });
  });

  describe('scss rules (source pins)', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/notifications/notifications.scss'), 'utf8');

    it('the footer link style: primary colour from a token, hover and focus-visible, 44 px high on a phone, no raw colour', () => {
      const rule = css.slice(css.indexOf('\n.notif-foot-link {'));
      const block = rule.slice(0, rule.indexOf('\n}\n'));
      expect(block).toMatch(/color:\s*var\(--app-primary\)/);
      expect(block).toMatch(/&:hover/);
      expect(block).toMatch(/&:focus-visible/);
      expect(block).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
      const phone = css.slice(css.indexOf('@media (max-width: 759.98px)'));
      expect(phone.slice(0, phone.indexOf('\n}\n'))).toMatch(/\.notif-foot-link\s*\{[^}]*min-height:\s*44px/);
    });

    it('the focus ring of the footer links and the overflow-note link is the --app-focus-ring token (as styles.scss)', () => {
      const foot = css.slice(css.indexOf('\n.notif-foot-link {'));
      expect(foot.slice(0, foot.indexOf('\n}\n'))).toMatch(/&:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--app-focus-ring\)/);
      const note = css.slice(css.indexOf('\n.notif-overflow {'));
      expect(note.slice(0, note.indexOf('\n}\n'))).not.toMatch(/focus-visible/);
    });

    it('the 到提醒中心查看 link in the overflow note is 44 px high on a phone', () => {
      const phone = css.slice(css.indexOf('@media (max-width: 759.98px)'));
      expect(phone.slice(0, phone.indexOf('\n}\n'))).toMatch(/\.notif-foot-link\s*\{[^}]*min-height:\s*44px/);
    });

    const block = (selector: string) => {
      const start = css.indexOf(`\n${selector} {`);
      expect(start, selector).toBeGreaterThanOrEqual(0);
      const rest = css.slice(start + 1);
      return rest.slice(0, rest.indexOf('\n}\n'));
    };

    it('the review pane is a card, sticky, and ends above the floating 記一筆 (the page\'s --fab-clear)', () => {
      const card = block('.notif-pane--card');
      expect(card).toMatch(/background:\s*var\(--app-surface\)/);
      expect(card).toMatch(/position:\s*sticky/);
      // Its top is the page's top padding at rest and when stuck (one offset, every breakpoint), so its height leaves
      // that padding above and the 記一筆 clearance below.
      expect(card).toMatch(/top:\s*var\(--page-pad-y\)/);
      expect(card).toMatch(/max-height:\s*calc\(100dvh - var\(--page-pad-y\) - var\(--fab-clear, 88px\)\)/);
      expect(card).not.toMatch(/top:\s*16px/);
      expect(card).toMatch(/overflow:\s*auto/);
      // Below panes the pane is no card: only the --card modifier carries surface, shadow and sticky.
      expect(css).not.toMatch(/\n\.notif-pane \{/);
    });

    it('on a phone the page heading (a focus target after the flow closes) scrolls clear of the tab bar and its + button', () => {
      const phone = css.slice(css.indexOf('@media (max-width: 759.98px)'));
      expect(phone.slice(0, phone.indexOf('\n}\n'))).toMatch(
        /app-page-header ::ng-deep h1\s*\{[^}]*scroll-margin-bottom:\s*calc\(var\(--m-nav-h\) \+ var\(--m-plus-lift\) \+ env\(safe-area-inset-bottom, 0px\) \+ 12px\)/,
      );
    });

    it('on a phone the failure banner centres its heading and icon on the 44 px 重試 (a block after the base rules)', () => {
      const phone = css.slice(css.lastIndexOf('@media (max-width: 759.98px)'));
      expect(css.indexOf('\n.notif-failed ::ng-deep {')).toBeLessThan(css.lastIndexOf('@media (max-width: 759.98px)'));
      const block = phone.slice(0, phone.indexOf('\n}\n'));
      expect(block).toMatch(/\.banner-body\s*\{[^}]*min-height:\s*44px/);
      expect(block).toMatch(/top:\s*clamp\(0px, calc\(56px - 100%\), 12px\)/);
    });

    it('a sheet open in the pane lifts the pane above the sidebar and the 記一筆 (z-index 1000) to the sheet\'s layer', () => {
      expect(css).toMatch(/\.notif-pane--card:has\(\.overlay\)\s*\{[^}]*z-index:\s*1100/);
    });

    it('with the split open the list column never gets narrower than a row needs (360 px)', () => {
      expect(block('.notif-split--open')).toMatch(/> \.notif-page \{[^}]*min-width:\s*360px/);
    });

    it('on a phone the header adds no bottom margin of its own: header to chips is the page gap (14 px), as chips to section', () => {
      const phone = css.slice(css.indexOf('@media (max-width: 759.98px)'));
      expect(phone).toMatch(/app-page-header\s*\{[^}]*margin-bottom:\s*0/);
    });
  });
});
