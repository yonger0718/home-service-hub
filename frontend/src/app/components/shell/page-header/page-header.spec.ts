import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LayoutMode, LayoutModeService, MATCH_MEDIA, PHONE_QUERY } from '../../../services/layout-mode.service';
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

@Component({
  imports: [PageHeaderComponent],
  template: `<app-page-header [back]="true"></app-page-header>`,
})
class BackHost {}

/** `/settings` reached by `urls` in order (the first is the router's first navigation); `/blocked` is always refused. */
async function renderBack(urls: string[], opts: { mode?: LayoutMode; hidden?: string[] } = {}) {
  if (opts.hidden) localStorage.setItem('hh-nav-hidden', JSON.stringify(opts.hidden));
  TestBed.configureTestingModule({
    providers: [
      provideRouter([
        { path: 'blocked', canActivate: [() => false], component: BlankPage },
        { path: '**', component: BlankPage },
      ]),
      provideLocationMocks(),
      { provide: MATCH_MEDIA, useValue: null },
      { provide: NavBadgeService, useValue: { badges: signal({}) } },
    ],
  });
  TestBed.inject(LayoutModeService).set(opts.mode ?? 'phone');
  const router = TestBed.inject(Router);
  for (const url of urls) await router.navigateByUrl(url);
  const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
  const navigate = vi.spyOn(router, 'navigateByUrl');
  const fixture = TestBed.createComponent(BackHost);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  return { el, back, navigate, button: el.querySelector<HTMLButtonElement>('button.page-back') };
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

  describe('back button (Settings as a pushed screen on phones)', () => {
    it('renders ‹ 返回 before the h1 on phones', async () => {
      const { el, button } = await renderBack(['/portfolio', '/settings']);

      expect(button).not.toBeNull();
      expect(button!.getAttribute('aria-label')).toBe('返回');
      expect(button!.getAttribute('type')).toBe('button');
      expect(button!.textContent?.trim()).toBe('‹');
      expect(el.querySelector('h1')!.previousElementSibling).toBe(button);
      expect(el.querySelector('h1')!.textContent?.trim()).toBe('設定');
    });

    it.each<LayoutMode>(['sheet', 'panes'])('is not rendered in %s mode', async mode => {
      const { button } = await renderBack(['/portfolio', '/settings'], { mode });

      expect(button).toBeNull();
    });

    it('is not rendered without the back input', async () => {
      const el = await render(375, '/settings');

      expect(el.querySelector('.page-back')).toBeNull();
    });

    it('goes back in history after an in-app navigation to /settings', async () => {
      const { button, back, navigate } = await renderBack(['/portfolio', '/settings']);

      button!.click();

      expect(back).toHaveBeenCalledTimes(1);
      expect(navigate).not.toHaveBeenCalled();
    });

    it('navigates to the landing path (/portfolio by default) on a first navigation to /settings', async () => {
      const { button, back, navigate } = await renderBack(['/settings']);

      button!.click();

      expect(back).not.toHaveBeenCalled();
      expect(navigate).toHaveBeenCalledWith('/portfolio');
    });

    it('navigates to the first visible group when 投資 is hidden', async () => {
      const { button, navigate } = await renderBack(['/settings'], { hidden: ['supplies', 'portfolio'] });

      button!.click();

      expect(navigate).toHaveBeenCalledWith('/accounting');
    });

    it('treats /settings as the first navigation when the one before it was refused', async () => {
      const { button, back, navigate } = await renderBack(['/blocked', '/settings']);

      button!.click();

      expect(back).not.toHaveBeenCalled();
      expect(navigate).toHaveBeenCalledWith('/portfolio');
    });

    it('is hidden on a first navigation when every hideable group is hidden (the fallback is /settings)', async () => {
      const { button } = await renderBack(['/settings'], { hidden: ['supplies', 'portfolio', 'accounting'] });

      expect(button).toBeNull();
    });

    it('still goes back with every hideable group hidden after an in-app navigation', async () => {
      const { button, back } = await renderBack(['/accounting', '/settings'], {
        hidden: ['supplies', 'portfolio', 'accounting'],
      });

      button!.click();

      expect(back).toHaveBeenCalledTimes(1);
    });

    it('shows no gear on /settings (it would link to itself)', async () => {
      const { el } = await renderBack(['/portfolio', '/settings']);

      expect(el.querySelector('a[aria-label="設定"]')).toBeNull();
      expect(el.querySelector('app-sub-nav')).not.toBeNull();
    });
  });
});
