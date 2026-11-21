import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { MATCH_MEDIA } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NavBadge } from '../shell/navigation';
import { MobileNavComponent } from './mobile-nav';

@Component({ template: '' })
class BlankPage {}

const BADGES: Record<string, NavBadge> = {
  accounting: { dot: true, label: '1 筆待確認' },
  import: { dot: true, label: '最近一次匯入失敗' },
};

/** The last `render`'s fixture, for a test that navigates after rendering. */
let rendered: ComponentFixture<MobileNavComponent>;

async function render(url: string, opts: { hidden?: string[]; badges?: boolean; values?: Record<string, NavBadge> } = {}): Promise<HTMLElement> {
  if (opts.hidden) localStorage.setItem('hh-nav-hidden', JSON.stringify(opts.hidden));
  if (opts.badges === false) localStorage.setItem('hh-nav-badges', '0');
  TestBed.configureTestingModule({
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      { provide: MATCH_MEDIA, useValue: () => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} }) },
      { provide: NavBadgeService, useValue: { badges: signal(opts.values ?? BADGES) } },
    ],
  });
  await TestBed.inject(Router).navigateByUrl(url);
  const fixture = TestBed.createComponent(MobileNavComponent);
  fixture.detectChanges();
  rendered = fixture;
  return fixture.nativeElement as HTMLElement;
}

function slots(el: HTMLElement): (string | undefined)[] {
  return Array.from(el.querySelector('.m-tabbar')!.children).map(child =>
    child.classList.contains('m-plus') ? '+' : child.querySelector('.tab-label')?.textContent?.trim(),
  );
}

function tab(el: HTMLElement, label: string): Element {
  return Array.from(el.querySelectorAll('.m-tab')).find(t => t.textContent?.includes(label))!;
}

describe('MobileNavComponent', () => {
  // Spec files share localStorage: `render({ hidden })` writes hh-nav-hidden, so clear it after each test too.
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('shows 投資 + 財務 by default (物資 hidden, gear in the header)', async () => {
    expect(slots(await render('/portfolio'))).toEqual(['投資', '+', '財務']);
  });

  it('shows every tab with the plus in the middle when all groups are visible', async () => {
    expect(slots(await render('/portfolio', { hidden: [] }))).toEqual(['物資', '投資', '+', '財務', '設定']);
  });

  it('links the plus to the entry page', async () => {
    const plus = (await render('/portfolio')).querySelector('a.m-plus')!;

    expect(plus.getAttribute('href')).toBe('/accounting/entry');
    expect(plus.getAttribute('aria-label')).toBe('新增記錄');
  });

  it('links the plus to the entry page with the calendar month and day', async () => {
    const el = await render('/accounting?month=2026-10&day=2026-10-21');
    expect(el.querySelector('a.m-plus')!.getAttribute('href')).toBe('/accounting/entry?month=2026-10&day=2026-10-21');
  });

  it("links the plus to a bare entry page from a page whose ?month is not the calendar's (報表)", async () => {
    const el = await render('/accounting/reports?month=2026-08');
    expect(el.querySelector('a.m-plus')!.getAttribute('href')).toBe('/accounting/entry');
  });

  it('follows the calendar day after a navigation', async () => {
    const el = await render('/portfolio');
    await TestBed.inject(Router).navigateByUrl('/accounting?month=2026-10&day=2026-10-22');
    rendered.detectChanges();
    expect(el.querySelector('a.m-plus')!.getAttribute('href')).toBe('/accounting/entry?month=2026-10&day=2026-10-22');
  });

  it('marks the active group tab', async () => {
    const el = await render('/accounting/accounts');

    expect(tab(el, '財務').getAttribute('aria-current')).toBe('page');
    expect(tab(el, '投資').getAttribute('aria-current')).toBeNull();
  });

  it('puts a dot on a non-active tab only', async () => {
    const onAccounting = await render('/accounting');
    expect(tab(onAccounting, '投資').querySelector('.tab-dot')).not.toBeNull();
    expect(tab(onAccounting, '財務').querySelector('.tab-dot')).toBeNull();
  });

  it('dots 財務 but not 投資 while on portfolio', async () => {
    const el = await render('/portfolio');

    expect(tab(el, '財務').querySelector('.tab-dot')).not.toBeNull();
    expect(tab(el, '投資').querySelector('.tab-dot')).toBeNull();
  });

  it('shows no dots when badges are off', async () => {
    const el = await render('/accounting', { badges: false });

    expect(el.querySelector('.tab-dot')).toBeNull();
    expect(el.querySelector('.m-tab .visually-hidden')).toBeNull();
  });

  it('a non-active group with a dot shows tab-dot; primary-only gets tab-dot--primary', async () => {
    const red = await render('/portfolio');
    expect(tab(red, '財務').querySelector('.tab-dot')).not.toBeNull();
    expect(tab(red, '財務').querySelector('.tab-dot--primary')).toBeNull();
    TestBed.resetTestingModule();
    localStorage.clear();

    const primary = await render('/portfolio', {
      values: { 'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' } },
    });
    expect(tab(primary, '財務').querySelector('.tab-dot.tab-dot--primary')).not.toBeNull();
  });

  it('a group with one red and one primary item shows a red tab dot', async () => {
    const el = await render('/portfolio', {
      values: {
        accounting: { dot: true, tone: 'red', label: '有逾期的卡費' },
        'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
      },
    });
    expect(tab(el, '財務').querySelector('.tab-dot')).not.toBeNull();
    expect(tab(el, '財務').querySelector('.tab-dot--primary')).toBeNull();
  });

  it('the active group shows none', async () => {
    const el = await render('/accounting');
    expect(tab(el, '財務').querySelector('.tab-dot')).toBeNull();
  });

  it('on /notifications no tab is highlighted and no tab has aria-current', async () => {
    const el = await render('/notifications');

    expect(el.querySelector('.m-tab.active')).toBeNull();
    expect(el.querySelector('.m-tab[aria-current]')).toBeNull();
    // No group is current, so every group's dot shows.
    expect(tab(el, '財務').querySelector('.tab-dot')).not.toBeNull();
    expect(tab(el, '投資').querySelector('.tab-dot')).not.toBeNull();
  });

  it('names the tab bar 分頁導覽', async () => {
    expect((await render('/portfolio')).querySelector('nav.m-tabbar')!.getAttribute('aria-label')).toBe('分頁導覽');
  });

  it('a dotted tab tells screen readers it has pending items', async () => {
    const el = await render('/portfolio');

    expect(tab(el, '財務').querySelector('.visually-hidden')?.textContent).toBe('，有待處理項目');
    expect(tab(el, '財務').textContent?.replace(/\s+/g, '')).toBe('財務，有待處理項目');
    expect(tab(el, '投資').querySelector('.visually-hidden')).toBeNull();
  });

  it('highlights no tab on /settings (a pushed screen), not even 設定', async () => {
    const el = await render('/settings', { hidden: [] });

    expect(el.querySelector('.m-tab.active')).toBeNull();
    expect(el.querySelector('.m-tab[aria-current]')).toBeNull();
    expect(tab(el, '設定').classList.contains('active')).toBe(false);
  });

  it('styles dots with the red-ink and primary tokens and has no count rule', () => {
    const scss = readFileSync(join(process.cwd(), 'src/app/components/mobile-nav/mobile-nav.scss'), 'utf8');
    expect(scss).toContain('var(--c-red-ink)');
    expect(scss).toContain('var(--app-primary)');
    expect(scss).not.toMatch(/\.tab-count|\.sub-count|\.item-count|\.group-count/);
  });
});
