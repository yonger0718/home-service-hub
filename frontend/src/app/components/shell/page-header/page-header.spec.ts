import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { MATCH_MEDIA, PHONE_QUERY } from '../../../services/layout-mode.service';
import { NavBadgeService } from '../../../services/nav-badge.service';
import { PageHeaderComponent } from './page-header';

@Component({ template: '' })
class BlankPage {}

@Component({
  imports: [PageHeaderComponent],
  template: `<app-page-header [title]="title"><button class="act">新增</button></app-page-header>`,
})
class Host {
  title: string | undefined = '我的標題';
}

async function render(width: number, url = '/accounting', hidden: string[] | null = null, title?: string) {
  if (hidden) localStorage.setItem('hh-nav-hidden', JSON.stringify(hidden));
  TestBed.configureTestingModule({
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      {
        provide: MATCH_MEDIA,
        useValue: (query: string) => ({
          media: query,
          matches: query === PHONE_QUERY ? width < 760 : false,
          addEventListener: () => {},
          removeEventListener: () => {},
        }),
      },
      { provide: NavBadgeService, useValue: { badges: signal({}) } },
    ],
  });
  await TestBed.inject(Router).navigateByUrl(url);
  const fixture = TestBed.createComponent(Host);
  fixture.componentInstance.title = title;
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('PageHeaderComponent', () => {
  beforeEach(() => localStorage.clear());

  it('renders the given title as h1 and projects actions', async () => {
    const el = await render(1440, '/accounting', null, '我的標題');

    expect(el.querySelector('h1')?.textContent?.trim()).toBe('我的標題');
    expect(el.querySelector('.page-actions .act')).not.toBeNull();
  });

  it('falls back to the active item title', async () => {
    const el = await render(1440, '/portfolio/transactions');

    expect(el.querySelector('h1')?.textContent?.trim()).toBe('股票交易紀錄');
  });

  it('shows the gear and sub-nav on phones when 物資 is hidden by default', async () => {
    const el = await render(375);

    expect(el.querySelector('a[aria-label="設定"]')?.getAttribute('href')).toBe('/settings');
    expect(el.querySelector('app-sub-nav')).not.toBeNull();
  });

  it('has no gear on phones when every group is visible', async () => {
    const el = await render(375, '/accounting', []);

    expect(el.querySelector('a[aria-label="設定"]')).toBeNull();
    expect(el.querySelector('app-sub-nav')).not.toBeNull();
  });

  it('has neither gear nor sub-nav at 760px and wider', async () => {
    const el = await render(800);

    expect(el.querySelector('a[aria-label="設定"]')).toBeNull();
    expect(el.querySelector('app-sub-nav')).toBeNull();
  });
});
