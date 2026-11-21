import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { MATCH_MEDIA, PHONE_QUERY } from '../../services/layout-mode.service';
import { NO_TASKS, NotifSummary, NotifTask, NotifTier, NotifTrackingRow, summarize } from './notif-task';
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
  task('d1', 'system'),
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
  const refresh = vi.fn();
  const invalidate = vi.fn();
  let width = 1440;

  const stub = {
    tasks,
    trackingRows,
    handled,
    loaded,
    loading: signal(false),
    round: signal(1),
    settledRound: signal(1),
    truncated: signal(false),
    failedSources: signal([]),
    nextUpcoming: signal(null),
    summary: signal<NotifSummary>(NO_TASKS),
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
    refresh.mockClear();
    invalidate.mockClear();
    stub.summary = signal(summarize(TASKS));
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
    it('reads in full at 1440', () => {
      stub.summary = signal(summarize([task('1', 'overdue'), task('2', 'overdue'), task('3', 'overdue'), ...TASKS.slice(2), task('x', 'today'), task('y', 'today')].slice(0, 7)));
      expect(summarize(tasks()).total).toBe(5);
      const s = stub.summary();
      expect(s.total).toBe(7);
      expect(s.overdue).toBe(3);
      expect(subtitle(mount(1440))).toBe('7 件要處理 · 3 件已逾期 · 數字只算要你動手的事');
    });
    it('drops the last part at 390', () => {
      stub.summary = signal(summarize([task('1', 'overdue'), task('2', 'overdue'), task('3', 'overdue'), task('4', 'today')]));
      expect(subtitle(mount(390))).toBe('4 件要處理 · 3 件已逾期');
    });
    it('has no 已逾期 part when none is overdue', () => {
      stub.summary = signal(summarize([task('1', 'today'), task('2', 'confirm')]));
      expect(subtitle(mount(1440))).toBe('2 件要處理 · 數字只算要你動手的事');
      TestBed.resetTestingModule();
      stub.summary = signal(summarize([task('1', 'today')]));
      expect(subtitle(mount(390))).toBe('1 件要處理');
    });
    it('reads 沒有要處理的事 at 0', () => {
      tasks.set([]);
      stub.summary = signal(NO_TASKS);
      expect(subtitle(mount(1440))).toBe('沒有要處理的事');
    });
  });

  describe('chips', () => {
    const chips = (root: HTMLElement) => Array.from(root.querySelectorAll<HTMLButtonElement>('.notif-chips button[role="radio"]'));

    it('is a labelled single-select radiogroup with counts: 全部 5 / 財務 5 / 投資 0, 全部 checked', () => {
      const root = mount();
      expect(root.querySelector('.notif-chips')?.getAttribute('role')).toBe('radiogroup');
      expect(root.querySelector('.notif-chips')?.getAttribute('aria-label')).toBe('來源');
      expect(chips(root).map(c => text(c))).toEqual(['全部5', '財務5', '投資0']);
      expect(chips(root).map(c => c.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
      expect(root.querySelectorAll('.chip-count').length).toBe(3);
    });

    it('counts never include tracking rows', () => {
      const root = mount();
      expect(text(chips(root)[0])).toBe('全部5');
    });

    it('picking 投資 leaves no row, shows 這個來源目前沒有要處理的事 and hides 追蹤中', async () => {
      const root = mount();
      chips(root)[2].click();
      await settle();
      expect(chips(root).map(c => c.getAttribute('aria-checked'))).toEqual(['false', 'false', 'true']);
      expect(root.querySelectorAll('app-notif-row').length).toBe(0);
      expect(text(root.querySelector('p.notif-none'))).toBe('這個來源目前沒有要處理的事');
      expect(root.querySelector('details.notif-tracking')).toBeNull();
    });

    it('picking 財務 keeps the ledger rows', async () => {
      const root = mount();
      chips(root)[1].click();
      await settle();
      expect(root.querySelectorAll('app-notif-row').length).toBe(5);
      expect(root.querySelector('p.notif-none')).toBeNull();
      expect(root.querySelector('details.notif-tracking')).not.toBeNull();
    });

    it('keeps focus on the chip that was pressed while the list changes', async () => {
      const root = mount();
      document.body.appendChild(root);
      const third = chips(root)[2];
      third.focus();
      third.click();
      await settle();
      expect(root.querySelectorAll('app-notif-row').length).toBe(0);
      expect(document.activeElement).toBe(chips(root)[2]);
      expect(chips(root)[2]).toBe(third);
      root.remove();
    });

    it('a chip with a count has an accessible name that includes it', () => {
      const root = mount();
      expect(chips(root)[1].getAttribute('aria-label')).toBe('財務，5 件');
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

  it('wires nothing on run yet (stub)', () => {
    const root = mount();
    expect(root.querySelectorAll('app-notif-row').length).toBe(5);
  });
});
