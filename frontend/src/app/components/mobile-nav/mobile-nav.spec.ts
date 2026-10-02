import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { NavGroupId } from '../shell/navigation';
import { MobileNavComponent } from './mobile-nav';

function render(activeId: string, groupId: NavGroupId): HTMLElement {
  TestBed.configureTestingModule({ imports: [MobileNavComponent], providers: [provideRouter([])] });
  const fixture = TestBed.createComponent(MobileNavComponent);
  fixture.componentRef.setInput('activeId', activeId);
  fixture.componentRef.setInput('activeGroupId', groupId);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('MobileNavComponent', () => {
  it('renders four group tabs with the centre plus between the second and third', () => {
    const el = render('inventory', 'supplies');

    const slots = Array.from(el.querySelector('.m-tabbar')!.children).map(child =>
      child.classList.contains('m-plus') ? '+' : child.textContent?.trim(),
    );
    expect(slots).toEqual(['物資', '投資', '+', '財務', '設定']);
    expect(el.querySelectorAll('.m-tab').length).toBe(4);
  });

  it('links the plus to the entry page from any group', () => {
    const el = render('portfolio', 'portfolio');

    const plus = el.querySelector('a.m-plus')!;
    expect(plus.getAttribute('href')).toBe('/accounting/entry');
    expect(plus.getAttribute('aria-label')).toBe('新增記錄');
  });

  it('shows the accounting sub-nav 紀錄 / 帳戶 / 設定', () => {
    const el = render('accounting-timeline', 'accounting');

    const links = Array.from(el.querySelectorAll('.m-subnav a'));
    expect(links.map(a => a.textContent?.trim())).toEqual(['紀錄', '帳戶', '設定']);
    expect(links.map(a => a.getAttribute('href'))).toEqual(['/accounting', '/accounting/accounts', '/accounting/settings']);
    expect(links[0].getAttribute('aria-current')).toBe('page');
  });
});
