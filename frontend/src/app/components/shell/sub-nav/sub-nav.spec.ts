import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { MATCH_MEDIA } from '../../../services/layout-mode.service';
import { NavBadgeService } from '../../../services/nav-badge.service';
import { NavBadge } from '../navigation';
import { SubNavComponent } from './sub-nav';

@Component({ template: '' })
class BlankPage {}

async function render(url: string, badges: Record<string, NavBadge> = {}, showBadges = true): Promise<HTMLElement> {
  localStorage.setItem('hh-nav-badges', showBadges ? '1' : '0');
  TestBed.configureTestingModule({
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      { provide: MATCH_MEDIA, useValue: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }) },
      { provide: NavBadgeService, useValue: { badges: signal(badges) } },
    ],
  });
  await TestBed.inject(Router).navigateByUrl(url);
  const fixture = TestBed.createComponent(SubNavComponent);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('SubNavComponent', () => {
  beforeEach(() => localStorage.clear());

  it('shows the accounting sub-nav 記錄 / 帳戶 / 記帳設定', async () => {
    const el = await render('/accounting');

    const links = Array.from(el.querySelectorAll('a'));
    expect(links.map(a => a.textContent?.trim())).toEqual(['記錄', '帳戶', '記帳設定']);
    expect(links.map(a => a.getAttribute('href'))).toEqual(['/accounting', '/accounting/accounts', '/accounting/settings']);
    expect(links[0].getAttribute('aria-current')).toBe('page');
    expect(el.querySelector('.seg-control')).not.toBeNull();
  });

  it('shows a count on the 帳戶 link and names it with the badge', async () => {
    const el = await render('/accounting', { accounting: { count: 1, label: '1 筆待確認' } });

    const link = el.querySelectorAll('a')[1];
    expect(link.textContent?.replace(/\s+/g, '')).toBe('帳戶1');
    expect(link.getAttribute('aria-label')).toBe('帳戶，1 筆待確認');
  });

  it('renders portfolio as chips with a dot on a failed 匯入', async () => {
    const el = await render('/portfolio', { import: { dot: true, label: '最近一次匯入失敗' } });

    const chips = Array.from(el.querySelectorAll('a'));
    expect(chips.map(a => a.textContent?.trim())).toEqual(['概覽', '交易', '股利', '已實現', '現金', '匯入']);
    expect(el.querySelector('.chips')).not.toBeNull();
    const imp = chips[5];
    expect(imp.querySelector('.sub-dot')).not.toBeNull();
    expect(imp.getAttribute('aria-label')).toBe('匯入，最近一次匯入失敗');
  });

  it('shows no count or dot when badges are off', async () => {
    const el = await render(
      '/portfolio',
      { import: { dot: true, label: '最近一次匯入失敗' }, accounting: { count: 1, label: '1 筆待確認' } },
      false,
    );

    expect(el.querySelector('.sub-dot, .sub-count')).toBeNull();
    expect(el.querySelectorAll('a')[5].getAttribute('aria-label')).toBe('匯入');
  });

  it('still shows the sub-nav of a hidden group route', async () => {
    const el = await render('/inventory');

    expect(Array.from(el.querySelectorAll('a')).map(a => a.textContent?.trim())).toEqual(['庫存', '採買']);
  });

  it('renders nothing for the single-item settings group', async () => {
    const el = await render('/settings');

    expect(el.querySelector('a')).toBeNull();
  });
});
