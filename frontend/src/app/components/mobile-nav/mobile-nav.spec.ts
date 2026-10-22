import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { MATCH_MEDIA } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NavBadge } from '../shell/navigation';
import { MobileNavComponent } from './mobile-nav';

@Component({ template: '' })
class BlankPage {}

const BADGES: Record<string, NavBadge> = {
  accounting: { count: 1, label: '1 筆待確認' },
  import: { dot: true, label: '最近一次匯入失敗' },
};

async function render(url: string, opts: { hidden?: string[]; badges?: boolean } = {}): Promise<HTMLElement> {
  if (opts.hidden) localStorage.setItem('hh-nav-hidden', JSON.stringify(opts.hidden));
  if (opts.badges === false) localStorage.setItem('hh-nav-badges', '0');
  TestBed.configureTestingModule({
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      { provide: MATCH_MEDIA, useValue: () => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} }) },
      { provide: NavBadgeService, useValue: { badges: signal(BADGES) } },
    ],
  });
  await TestBed.inject(Router).navigateByUrl(url);
  const fixture = TestBed.createComponent(MobileNavComponent);
  fixture.detectChanges();
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
  beforeEach(() => localStorage.clear());

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
});
