import { HttpErrorResponse } from '@angular/common/http';
import { computed, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { Subject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ScheduleCatchUpResult, ScheduleInstance } from '../../models/accounting.model';
import { AccountingService } from '../../services/accounting.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { ToastService } from '../../services/toast.service';
import { IMPORT_RUNNING_TOAST } from '../accounting/schedule-math';
import { NotificationCenterService } from '../../services/notification-center.service';
import { MATCH_MEDIA, PHONE_QUERY } from '../../services/layout-mode.service';
import { NotifSummary, NotifTask, NotifTier, NotifTrackingRow, summarize } from './notif-task';
import { NOTIF_PRIMARY_CONTROL, NotifRowComponent } from './notif-row/notif-row';
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

const TASKS: NotifTask[] = [
  task('a1', 'overdue', { sortKey: '2026-10-01' }),
  task('a2', 'overdue', { sortKey: '2026-10-02' }),
  task('b1', 'today'),
  task('c1', 'confirm'),
  task('d1', 'system', { source: 'portfolio', from: 'importReview' }),
];
const ROWS: NotifTrackingRow[] = [
  { id: 'debts', title: '借還款 · 3 位', meta: '甲、乙、丙', count: 3, link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'debts' } } },
  { id: 'upcoming', title: '即將到來 · 2 件', meta: '', count: 2, link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'cards' } } },
];

describe('NotificationsComponent', () => {
  const tasks = signal<readonly NotifTask[]>(TASKS);
  const trackingRows = signal<readonly NotifTrackingRow[]>(ROWS);
  const handled = signal<string | null>('3 件');
  const loaded = signal(true);
  const round = signal(1);
  const settledRound = signal(1);
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

  const stub = {
    tasks,
    trackingRows,
    handled,
    loaded,
    loading: signal(false),
    round,
    settledRound,
    truncated: signal(false),
    failedSources: signal([]),
    nextUpcoming: signal(null),
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
    refresh.mockClear();
    invalidate.mockClear();
    accounting.postScheduleInstance.mockClear();
    accounting.catchUpSchedule.mockClear();
    posts = [];
    catchUps = [];
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
      expect(root.querySelector('.page-subtitle')).toBeNull();
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
    it('also hides the tasks and 追蹤中 that the service already holds', () => {
      loaded.set(false);
      const root = mount();
      expect(root.querySelectorAll('app-notif-row').length).toBe(0);
      expect(root.querySelector('details.notif-tracking')).toBeNull();
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
      // Everything done: the note goes too (Task 10 owns what shows instead).
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
      expect(text(a)).toBe('已處理的對帳（近 7 天）3 件 ›');
      expect(a.getAttribute('href')).toBe('/accounting/reminders?tab=todo');
    });
    it('handled link is absent when handled is null', () => {
      handled.set(null);
      expect(mount().querySelector('a.notif-handled')).toBeNull();
    });
    it('the 提醒中心 link is always there', () => {
      const a = mount().querySelector('a.notif-reminders')!;
      expect(text(a)).toBe('提醒中心（借還款、週期與分期管理）');
      expect(a.getAttribute('href')).toBe('/accounting/reminders');
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

    it('a catch-up that stops midway shows 失敗 with the count and the reason, without 重試', async () => {
      const root = open();
      await press(root, FEE.id);
      await answer(catchUps[0], catchUp([31, 32], { instance_id: 33, error: '帳戶已封存' }));
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試月費：已入帳 2 期，之後的入帳失敗（帳戶已封存）', action: null }),
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

    it('a 500 shows 失敗 with 重試, which posts the same instance again', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 500);
      expect(toasts.current()).toEqual(
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：儲存失敗，請稍後再試', action: expect.objectContaining({ label: '重試' }) }),
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
        expect.objectContaining({ kind: 'error', text: '測試月費 入帳失敗：無法連線到伺服器', action: expect.objectContaining({ label: '重試' }) }),
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
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：儲存失敗，請稍後再試', action: expect.objectContaining({ label: '重試' }) }),
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

    it('a 422 shows the server message without 重試', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 422, { code: 'VALIDATION', message: '帳戶已封存' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：帳戶已封存', action: null }));
    });

    it('a 409 already_posted says the period is posted, without 重試', async () => {
      const root = open();
      await press(root, RENT.id);
      await fail(posts[0], 409, { code: 'CONFLICT', message: 'already_posted' });
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：此期已入帳', action: null }));
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
        expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：儲存失敗，請稍後再試', action: null }),
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
});
