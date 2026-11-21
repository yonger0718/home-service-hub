import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { NavBadge } from '../components/shell/navigation';
import { NotifSummary } from '../components/notifications/notif-task';
import { BADGE_REFRESH_MIN_MS, NavBadgeService } from './nav-badge.service';
import * as centre from './notification-center.service';

/**
 * The facade only (R21): the counting, rounds and dots are `NotificationCenterService`'s and its spec's
 * (`notification-center.service.spec.ts`, `notification-center.rounds.spec.ts`).
 */
describe('NavBadgeService (facade)', () => {
  const badges = signal<Readonly<Record<string, NavBadge>>>({ import: { dot: true, tone: 'red', label: '最近一次匯入失敗' } });
  const summary = signal<NotifSummary>({ total: 2, overdue: 1, tone: 'red', label: '通知，2 件要處理，1 件已逾期' });
  const start = vi.fn();
  const refresh = vi.fn();

  function facade(): NavBadgeService {
    TestBed.configureTestingModule({
      providers: [{ provide: centre.NotificationCenterService, useValue: { badges, summary, start, refresh } }],
    });
    return TestBed.inject(NavBadgeService);
  }

  afterEach(() => {
    TestBed.resetTestingModule();
    vi.clearAllMocks();
  });

  it("badges and summary are the centre's signals", () => {
    const nav = facade();
    expect(nav.badges).toBe(badges);
    expect(nav.summary).toBe(summary);
  });

  it('start() and refresh() delegate', () => {
    const nav = facade();
    nav.start();
    expect(start).toHaveBeenCalledTimes(1);
    nav.refresh();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledWith({ reason: 'focus' });
  });

  it('re-exports the centre\'s throttle', () => {
    expect(BADGE_REFRESH_MIN_MS).toBe(centre.BADGE_REFRESH_MIN_MS);
    expect(BADGE_REFRESH_MIN_MS).toBe(30_000);
  });
});
