import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, RouterLink, convertToParamMap, provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { BehaviorSubject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, EntryGroupSummary } from '../../../models/accounting.model';
import { formatMoney } from '../format';
import { makeEntryDetail, makeGroupMember } from '../testing/fixtures';
import { SplitGroupComponent } from './split-group';

const GROUP: EntryGroupSummary = {
  id: 4, kind: 'split', name: '東京旅行', merchant: '成田機場', description: '整趟行程', count: 3, total: '-1500.0000', currency: 'TWD',
};

/** A three-member split over two accounts and two currencies; the JPY member is protected (a settlement). */
const MEMBERS = [
  makeGroupMember({ id: 42, amount: '-1200.0000', name: '機票', account_id: 1, account_name: '玉山 UNI', group: GROUP }),
  makeGroupMember({ id: 43, amount: '-300.0000', category: '交通/計程車', account_id: 1, account_name: '玉山 UNI', group: GROUP }),
  makeGroupMember({
    id: 44, amount: '2000', currency: 'JPY', name: '退稅', account_id: 9, account_name: '日幣現金', group: GROUP,
    protected: true, protected_reason: 'settlement',
  }),
];

function splitDetail(overrides: Partial<EntryDetail> = {}): EntryDetail {
  return makeEntryDetail({ id: 42, amount: '-1200.0000', name: '機票', group: GROUP, group_members: MEMBERS, entry_time: '08:15:00', ...overrides });
}

describe('SplitGroupComponent (多類別 group view)', () => {
  let http: HttpTestingController;
  let params: BehaviorSubject<ParamMap>;
  let router: Router;
  let location: Location;

  beforeEach(async () => {
    params = new BehaviorSubject(convertToParamMap({ id: '42' }));
    await TestBed.configureTestingModule({
      imports: [SplitGroupComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        provideLocationMocks(),
        { provide: ActivatedRoute, useValue: { paramMap: params } },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    location = TestBed.inject(Location);
  });

  afterEach(() => http.verify());

  function render(body: EntryDetail = splitDetail()): ComponentFixture<SplitGroupComponent> {
    const fixture = TestBed.createComponent(SplitGroupComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(body);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<SplitGroupComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  /** The navigation state the group view was opened with (as `router.currentNavigation()` would report it). */
  function openedWith(state: Record<string, unknown>): void {
    vi.spyOn(router, 'currentNavigation').mockReturnValue({ extras: { state } } as never);
  }

  it('shows the net per currency from the server-signed amounts, protected members included', () => {
    const fixture = render();
    const nets = Array.from(el(fixture).querySelectorAll('.group-net')).map(node => node.textContent?.replace(/\s+/g, ' ').trim());
    expect(nets).toEqual([`TWD ${formatMoney(-1500, 'TWD', { sign: true })}`, `JPY ${formatMoney(2000, 'JPY', { sign: true })}`]);
    expect(el(fixture).querySelector('.group-net')?.classList).toContain('neg');
  });

  it('shows one net without a currency label for a single-currency split', () => {
    const fixture = render(splitDetail({ group_members: MEMBERS.slice(0, 2) }));
    const nets = Array.from(el(fixture).querySelectorAll('.group-net')).map(node => node.textContent?.trim());
    expect(nets).toEqual([formatMoney(-1500, 'TWD', { sign: true })]);
  });

  it('shows the folder tile, the name, N 項 · M 個帳戶, 商家, 日期／時間 and 整筆備註', () => {
    const fixture = render();
    expect(el(fixture).querySelector('.group-head app-split-folder')).not.toBeNull();
    expect(el(fixture).querySelector('.group-head .cat')?.textContent?.trim()).toBe('多類別');
    expect(el(fixture).querySelector('.group-name')?.textContent?.trim()).toBe('東京旅行');
    expect(el(fixture).querySelector('.group-count')?.textContent?.trim()).toBe('3 項 · 2 個帳戶');
    expect(el(fixture).querySelector('.group-merchant')?.textContent?.trim()).toBe('成田機場');
    expect(el(fixture).querySelector('.group-date')?.textContent?.trim()).toBe('2026/10/02');
    expect(el(fixture).querySelector('.group-time')?.textContent?.trim()).toBe('12:30');
    expect(el(fixture).querySelector('.group-note')?.textContent?.trim()).toBe('整趟行程');
  });

  it('falls back to 多類別 for an unnamed split', () => {
    const fixture = render(splitDetail({ group: { ...GROUP, name: null } }));
    expect(el(fixture).querySelector('.group-name')?.textContent?.trim()).toBe('多類別');
  });

  it('lists every child with its icon, name, account, signed amount and a lock glyph on a protected one', () => {
    const fixture = render();
    const rows = Array.from(el(fixture).querySelectorAll<HTMLAnchorElement>('.split-child'));
    expect(rows.map(row => row.dataset['memberId'])).toEqual(['42', '43', '44']);
    expect(rows[0].querySelector('.ico')?.textContent?.trim()).toBe('🍜');
    expect(rows[0].querySelector('.split-child-name')?.textContent).toContain('機票');
    expect(rows[0].querySelector('.split-child-name')?.textContent).toContain('玉山 UNI');
    expect(rows[1].querySelector('.split-child-name b')?.textContent?.trim()).toBe('計程車');
    expect(rows[0].querySelector('.split-child-amount')?.textContent?.trim()).toBe(formatMoney(-1200, 'TWD', { sign: true }));
    expect(rows.map(row => row.querySelector('.split-child-lock') !== null)).toEqual([false, false, true]);
    expect(rows[2].querySelector('.split-child-lock')?.getAttribute('aria-label')).toBe('受保護');
  });

  it('opens a child with a push one level above the group view (viaGroup), carrying the group view state', () => {
    openedWith({ groupDepth: 1 });
    const fixture = render();
    const links = fixture.debugElement.queryAll(By.directive(RouterLink)).map(debug => debug.injector.get(RouterLink));
    const child = links.find(link => link.href === '/accounting/entries/43')!;
    expect(child).toBeDefined();
    expect(child.replaceUrl).toBeFalsy();
    expect(child.state).toEqual({ groupDepth: 2, viaGroup: true });
  });

  it('opens children in place once 「← 多類別」 put this view in a child\'s place (viaGroup), keeping the depth', () => {
    openedWith({ groupDepth: 2, viaGroup: true });
    const fixture = render();
    const links = fixture.debugElement.queryAll(By.directive(RouterLink)).map(debug => debug.injector.get(RouterLink));
    const child = links.find(link => link.href === '/accounting/entries/43')!;
    expect(child.replaceUrl).toBe(true);
    expect(child.state).toEqual({ groupDepth: 2, viaGroup: true });
  });

  it('keeps the passbook in the child links when opened from a passbook', () => {
    params.next(convertToParamMap({ id: '3', eid: '42' }));
    const fixture = render();
    expect(el(fixture).querySelector<HTMLAnchorElement>('.split-child')?.getAttribute('href')).toBe('/accounting/accounts/3/entries/42');
  });

  it('編輯 opens the entry form for the split with the parent bubble selected', () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const fixture = render();
    el(fixture).querySelector<HTMLButtonElement>('.action-edit')!.click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 42, 'edit'], { queryParams: { select: 'parent' } });
  });

  it('disables 編輯 and 刪除整組 on a locked split and offers no 複製', () => {
    const fixture = render(splitDetail({ locked: true }));
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-edit')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.disabled).toBe(true);
    expect(el(fixture).querySelector('.action-copy')).toBeNull();
  });

  it('刪除整組 asks first, lists the members, deletes the whole split and closes', () => {
    openedWith({ groupDepth: 1 });
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    const go = vi.spyOn(location, 'historyGo');
    const fixture = render();
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    const dialog = el(fixture).querySelector('.delete-confirm[role="alertdialog"]')!;
    expect(dialog.textContent).toContain('東京旅行');
    expect(dialog.querySelectorAll('li')).toHaveLength(3);

    dialog.querySelector<HTMLButtonElement>('.delete-group')!.click();
    const req = http.expectOne('/api/accounting/splits/4');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
    expect(go).toHaveBeenCalledWith(-1);
  });

  it('Esc closes the delete confirmation first and is marked handled', () => {
    const fixture = render();
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el(fixture).querySelector('.delete-confirm')!.dispatchEvent(event);
    fixture.detectChanges();
    expect(event.defaultPrevented).toBe(true);
    expect(el(fixture).querySelector('.delete-confirm')).toBeNull();
  });

  it('✕ opened from a list row goes back to that list by its depth (1, or 2 after 「← 多類別」 replaced a child)', () => {
    openedWith({ groupDepth: 1 });
    Object.defineProperty(router, 'lastSuccessfulNavigation', { configurable: true, value: () => ({ previousNavigation: {} }) });
    const go = vi.spyOn(location, 'historyGo');
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const fixture = render();
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(go).toHaveBeenLastCalledWith(-1);

    openedWith({ groupDepth: 2 });
    params.next(convertToParamMap({ id: '43' }));
    http.expectOne('/api/accounting/entries/43').flush(splitDetail({ id: 43 }));
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(go).toHaveBeenLastCalledWith(-2);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('✕ on a deep-linked group view goes to the timeline, never back', () => {
    const back = vi.spyOn(location, 'historyGo');
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const fixture = render();
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(back).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/accounting');
  });

  it('✕ follows the entry-detail closeTo rule: the reminder centre, and the passbook it was opened from', () => {
    openedWith({ closeTo: 'reminders', groupDepth: 1 });
    const navigate = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const fixture = render();
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(navigate).toHaveBeenLastCalledWith('/accounting/reminders');

    vi.spyOn(router, 'currentNavigation').mockReturnValue({ extras: { state: {} } } as never);
    params.next(convertToParamMap({ id: '3', eid: '42' }));
    http.expectOne('/api/accounting/entries/42').flush(splitDetail());
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.dhead .close')!.click();
    expect(navigate).toHaveBeenLastCalledWith('/accounting/accounts/3');
  });

  it('hands a dissolved split over to the entry detail in place (replaceUrl)', () => {
    openedWith({ groupDepth: 1 });
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    render(makeEntryDetail({ id: 42 }));
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 42], { replaceUrl: true, state: { groupDepth: 1 } });
  });

  it('shows the load error with a ✕ when the member cannot be read', () => {
    const fixture = TestBed.createComponent(SplitGroupComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush('no', { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();
    expect(el(fixture).querySelector('.load-error')).not.toBeNull();
    expect(el(fixture).querySelector('.error-head .close')).not.toBeNull();
  });

  it('draws the folder tile of its members, +N past four and the badge of the count', () => {
    const icons = ['🍜', '🚕', '🎬', '📱', '🎁'];
    const group = { ...GROUP, count: 5 };
    const members = icons.map((icon, i) => makeGroupMember({ id: 70 + i, group, category_icon: icon, category_color: '#4a90e2' }));
    const fixture = render(splitDetail({ group, group_members: members }));
    const folder = el(fixture).querySelector<HTMLElement>('.group-head app-split-folder.folder')!;
    expect(folder.classList.contains('compact')).toBe(false);
    const cells = Array.from(folder.querySelectorAll<HTMLElement>('.cell'));
    expect(cells.map(cell => cell.textContent)).toEqual(['🍜', '🚕', '🎬', '+2']);
    expect(cells[0].style.background).toBe('rgb(74, 144, 226)');
    expect(folder.querySelector('.badge')?.textContent).toBe('5');
    expect(el(fixture).querySelectorAll('.split-child')).toHaveLength(5);
  });
});
