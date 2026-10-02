import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';
import { DockComponent } from './components/dock/dock';
import { NAV_GROUPS, navItemForUrl } from './components/shell/navigation';

const REMOVED_ACCOUNTING_PAGES = ['dashboard', 'transactions', 'cards', 'categories', 'recurring'];

const ACCOUNTING_URLS = [
  '/accounting',
  '/accounting/accounts',
  '/accounting/accounts/new',
  '/accounting/accounts/3',
  '/accounting/accounts/3/settings',
  '/accounting/entry',
  '/accounting/entry?kind=income',
  '/accounting/entries/5',
  '/accounting/entries/5/edit',
  '/accounting/settings',
];

async function navigate(url: string): Promise<RouterTestingHarness> {
  TestBed.configureTestingModule({
    providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
  });
  const harness = await RouterTestingHarness.create();
  await harness.navigateByUrl(url);
  return harness;
}

describe('accounting routes', () => {
  it('redirects the removed accounting pages to the timeline', () => {
    for (const removed of REMOVED_ACCOUNTING_PAGES) {
      const route = routes.find(r => r.path === `accounting/${removed}`);
      expect(route?.redirectTo).toBe('accounting');
    }
  });

  it.each(ACCOUNTING_URLS)('resolves %s inside the accounting layout', async url => {
    const harness = await navigate(url);

    expect(TestBed.inject(Router).url).toBe(url);
    expect(harness.routeNativeElement?.tagName.toLowerCase()).toBe('app-accounting-layout');
  });

  it('lands a bookmarked /accounting/cards on the timeline', async () => {
    await navigate('/accounting/cards');

    expect(TestBed.inject(Router).url).toBe('/accounting');
  });

  it('points the accounting navigation at 紀錄, 帳戶 and the accounting settings page', () => {
    const accounting = NAV_GROUPS.find(group => group.id === 'accounting')!;
    expect(accounting.defaultPath).toBe('/accounting');
    expect(accounting.items.map(item => [item.label, item.path])).toEqual([
      ['紀錄', '/accounting'],
      ['帳戶', '/accounting/accounts'],
      ['設定', '/accounting/settings'],
    ]);
  });

  it('keeps the global settings page reachable as its own group', () => {
    expect(NAV_GROUPS.map(group => group.id)).toEqual(['supplies', 'portfolio', 'accounting', 'settings']);
    expect(navItemForUrl('/settings').path).toBe('/settings');
  });

  it('maps accounting URLs to their navigation items', () => {
    expect(navItemForUrl('/accounting').label).toBe('紀錄');
    expect(navItemForUrl('/accounting/entries/5').label).toBe('紀錄');
    expect(navItemForUrl('/accounting/entry').label).toBe('紀錄');
    expect(navItemForUrl('/accounting/accounts/5').label).toBe('帳戶');
    expect(navItemForUrl('/accounting/settings').label).toBe('設定');
    expect(navItemForUrl('/accounting/settings').group).toBe('accounting');
  });

  it('keeps the Accounting dock item highlighted on an account history page', () => {
    TestBed.configureTestingModule({ imports: [DockComponent], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(DockComponent);

    fixture.componentRef.setInput('activeId', navItemForUrl('/accounting/accounts/5').id);
    fixture.detectChanges();

    const active = (fixture.nativeElement as HTMLElement).querySelector('.dock-item[aria-current="page"]');
    expect(navItemForUrl('/accounting/accounts/5').id).toBe('accounting');
    expect(active?.getAttribute('aria-label')).toBe('記帳帳戶');
    expect(active?.classList).toContain('active');
  });
});
