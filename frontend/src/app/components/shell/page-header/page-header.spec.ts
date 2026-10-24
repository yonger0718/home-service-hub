import { Location } from '@angular/common';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { provideLocationMocks } from '@angular/common/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LayoutMode, LayoutModeService, MATCH_MEDIA, PHONE_QUERY } from '../../../services/layout-mode.service';
import { NavBadgeService } from '../../../services/nav-badge.service';
import { PageHeaderComponent, PageMenuItem } from './page-header';

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
  template: `<app-page-header title="投資概覽" [subtitle]="subtitle"></app-page-header>`,
})
class SubtitleHost {
  subtitle: string | null = '行情更新於 13:30 · 台股收盤';
}

@Component({
  imports: [PageHeaderComponent],
  template: `<app-page-header title="T" [menu]="items"><button class="primary">主要</button></app-page-header>`,
})
class MenuHost {
  ran = 0;
  items: PageMenuItem[] = [
    { label: '匯入券商', icon: 'pi-upload', routerLink: '/portfolio/import', queryParams: { type: 'broker' } },
    { label: '刷新行情', icon: 'pi-refresh', run: () => this.ran++ },
    { label: '停用', run: () => this.ran++, disabled: true },
  ];
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

async function render(
  width: number,
  url = '/accounting',
  hidden: string[] | null = null,
  title?: string,
) {
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
  beforeEach(() => localStorage.removeItem('hh-nav-hidden'));
  // Later spec files share this localStorage: never leave hh-nav-hidden behind.
  afterEach(() => localStorage.removeItem('hh-nav-hidden'));

  it('renders the given title as h1 and projects actions', async () => {
    const el = await render(1440, '/accounting', null, '我的標題');

    expect(el.querySelector('h1')?.textContent?.trim()).toBe('我的標題');
    expect(el.querySelector('.page-actions .act')).not.toBeNull();
  });

  describe('subtitle', () => {
    function renderSubtitle(subtitle: string | null) {
      TestBed.configureTestingModule({
        providers: [
          provideRouter([{ path: '**', component: BlankPage }]),
          { provide: MATCH_MEDIA, useValue: null },
          { provide: NavBadgeService, useValue: { badges: signal({}) } },
        ],
      });
      const fixture = TestBed.createComponent(SubtitleHost);
      fixture.componentInstance.subtitle = subtitle;
      fixture.detectChanges();
      return fixture.nativeElement as HTMLElement;
    }

    it('renders a muted line under the h1', () => {
      const el = renderSubtitle('行情更新於 13:30 · 台股收盤');
      const subtitle = el.querySelector('.page-subtitle');

      expect(subtitle?.textContent?.trim()).toBe('行情更新於 13:30 · 台股收盤');
      expect(el.querySelector('h1')!.nextElementSibling).toBe(subtitle);
      expect(subtitle!.parentElement!.classList).toContain('page-title');
    });

    it('is not rendered without a subtitle', () => {
      const el = renderSubtitle(null);

      expect(el.querySelector('.page-subtitle')).toBeNull();
      expect(el.querySelector('h1')?.textContent?.trim()).toBe('投資概覽');
    });
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
      expect(el.querySelector('.page-title')!.previousElementSibling).toBe(button);
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
  describe('⋯ menu (phone)', () => {
    async function renderMenu(mode: LayoutMode = 'phone', items?: PageMenuItem[]) {
      TestBed.configureTestingModule({
        providers: [
          provideRouter([{ path: '**', component: BlankPage }]),
          { provide: MATCH_MEDIA, useValue: null },
          { provide: NavBadgeService, useValue: { badges: signal({}) } },
        ],
      });
      TestBed.inject(LayoutModeService).set(mode);
      await TestBed.inject(Router).navigateByUrl('/accounting');
      const fixture = TestBed.createComponent(MenuHost);
      if (items) fixture.componentInstance.items = items;
      document.body.appendChild(fixture.nativeElement);
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      const flush = async () => {
        fixture.detectChanges();
        await fixture.whenStable();
        await new Promise<void>(r => setTimeout(r));
        fixture.detectChanges();
      };
      const more = () => el.querySelector<HTMLButtonElement>('.header-more');
      const menuItems = () => Array.from(el.querySelectorAll<HTMLElement>('[role="menuitem"]'));
      const panel = () => el.querySelector<HTMLElement>('.header-menu');
      const key = (k: string) => {
        const ev = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
        panel()!.dispatchEvent(ev);
        return ev;
      };
      return { fixture, el, flush, more, menuItems, panel, key, host: fixture.componentInstance };
    }

    afterEach(() => document.body.replaceChildren());

    it('renders a 更多動作 button after the actions and before the gear, closed', async () => {
      const { el, more, panel } = await renderMenu();
      const b = more()!;

      expect(b.getAttribute('aria-label')).toBe('更多動作');
      expect(b.getAttribute('aria-haspopup')).toBe('menu');
      expect(b.getAttribute('aria-expanded')).toBe('false');
      const actions = el.querySelector('.page-actions')!;
      const gear = el.querySelector('.header-gear')!;
      expect(gear).not.toBeNull();
      expect(actions.nextElementSibling).toBe(b.parentElement);
      expect(b.parentElement!.nextElementSibling).toBe(gear);
      expect(panel()).toBeNull();
    });

    it('renders no menu from 760 px or with no items, and the gear then follows the actions', async () => {
      const sheet = await renderMenu('sheet');
      expect(sheet.more()).toBeNull();
      TestBed.resetTestingModule();
      const empty = await renderMenu('phone', []);
      expect(empty.more()).toBeNull();
      expect(empty.el.querySelector('.page-actions')!.nextElementSibling).toBe(empty.el.querySelector('.header-gear'));
    });

    it('opens on click, focuses the first item and lists the items in order', async () => {
      const { more, menuItems, panel, flush } = await renderMenu();
      more()!.click();
      await flush();

      expect(panel()!.getAttribute('role')).toBe('menu');
      expect(more()!.getAttribute('aria-expanded')).toBe('true');
      expect(more()!.getAttribute('aria-controls')).toBe(panel()!.id);
      const items = menuItems();
      expect(items.map(i => i.textContent!.trim())).toEqual(['匯入券商', '刷新行情', '停用']);
      expect(document.activeElement).toBe(items[0]);
      expect(items[0].tagName).toBe('A');
      expect(items[0].getAttribute('href')).toContain('/portfolio/import?type=broker');
      expect(items[2].getAttribute('aria-disabled')).toBe('true');
    });

    it('runs an item and closes', async () => {
      const { more, menuItems, panel, flush, host } = await renderMenu();
      more()!.click();
      await flush();
      menuItems()[2].click();
      await flush();
      expect(host.ran).toBe(0);
      expect(panel()).not.toBeNull();

      menuItems()[1].click();
      await flush();
      expect(host.ran).toBe(1);
      expect(panel()).toBeNull();
      expect(document.activeElement).toBe(more());
    });

    it('arrow keys cycle, Home/End jump, Escape closes and restores focus', async () => {
      const { more, menuItems, panel, flush, key } = await renderMenu();
      more()!.click();
      await flush();
      const items = menuItems();

      key('ArrowDown');
      key('ArrowDown');
      expect(document.activeElement).toBe(items[2]);
      key('ArrowDown');
      expect(document.activeElement).toBe(items[0]);
      key('ArrowUp');
      expect(document.activeElement).toBe(items[2]);
      key('Home');
      expect(document.activeElement).toBe(items[0]);
      key('End');
      expect(document.activeElement).toBe(items[2]);
      key('Escape');
      await flush();
      expect(panel()).toBeNull();
      expect(document.activeElement).toBe(more());
    });

    it('closes on an outside pointerdown and when focus leaves the menu', async () => {
      const { el, more, menuItems, panel, flush } = await renderMenu();
      more()!.click();
      await flush();
      document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      await flush();
      expect(panel()).toBeNull();

      more()!.click();
      await flush();
      panel()!.dispatchEvent(
        new FocusEvent('focusout', { bubbles: true, relatedTarget: el.querySelector('.primary') }),
      );
      await flush();
      expect(panel()).toBeNull();

      more()!.click();
      await flush();
      panel()!.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: menuItems()[1] }));
      await flush();
      expect(panel()).not.toBeNull();
    });

    it('Home and End are handled (no page scroll)', async () => {
      const { more, menuItems, flush, key } = await renderMenu();
      more()!.click();
      await flush();

      const ev = key('End');

      expect(ev.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(menuItems()[2]);
    });
    it('keeps the menu open when focus blurs to null during a press inside it (Safari tap)', async () => {
      const { more, menuItems, panel, flush, host } = await renderMenu();
      more()!.click();
      await flush();
      const item = menuItems()[1];

      item.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      panel()!.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
      await flush();
      expect(panel()).not.toBeNull();

      item.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
      item.click();
      await flush();
      expect(host.ran).toBe(1);
      expect(panel()).toBeNull();
    });

    it('closes after a routerLink item is clicked', async () => {
      const { more, menuItems, panel, flush } = await renderMenu();
      more()!.click();
      await flush();
      menuItems()[0].addEventListener('click', e => e.preventDefault());
      menuItems()[0].click();
      await flush();
      expect(panel()).toBeNull();
    });

    it('renders a disabled routerLink item as a button, never a link', async () => {
      const { more, menuItems, flush } = await renderMenu('phone', [
        { label: '停用連結', routerLink: '/x', disabled: true },
      ]);
      more()!.click();
      await flush();
      expect(menuItems()[0].tagName).toBe('BUTTON');
      expect(menuItems()[0].getAttribute('aria-disabled')).toBe('true');
    });

    it('closes when the layout leaves phone while open', async () => {
      const { more, panel, flush, el } = await renderMenu();
      more()!.click();
      await flush();
      TestBed.inject(LayoutModeService).set('sheet');
      await flush();
      expect(panel()).toBeNull();
      TestBed.inject(LayoutModeService).set('phone');
      await flush();
      expect(more()!.getAttribute('aria-expanded')).toBe('false');
      expect(el.querySelector('.header-menu')).toBeNull();
    });

    it('has no aria-controls while closed', async () => {
      const { more } = await renderMenu();
      expect(more()!.hasAttribute('aria-controls')).toBe(false);
    });

    it('removes the document pointerdown listener when destroyed while open', async () => {
      const { more, flush, fixture } = await renderMenu();
      more()!.click();
      await flush();
      const spy = vi.spyOn(document, 'removeEventListener');
      fixture.destroy();
      expect(spy.mock.calls.some(c => c[0] === 'pointerdown')).toBe(true);
    });
  });
});

describe('PageHeader phone stylesheet (polish-layout-v2 D9)', () => {
  it('keeps the title block to one line so the row never grows past the icon buttons', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/shell/page-header/page-header.scss'), 'utf8');
    const start = css.indexOf('@media (max-width: 759.98px)');
    expect(start).toBeGreaterThan(-1);
    const flat = css.slice(start).replace(/\s+/g, ' ');
    expect(flat).toMatch(/ h1 \{[^}]*font-size: 20px;[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
    expect(flat).toMatch(/\.page-subtitle \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
    expect(flat).toMatch(/\.page-actions \{[^}]*flex-wrap: nowrap;/);
  });
});
