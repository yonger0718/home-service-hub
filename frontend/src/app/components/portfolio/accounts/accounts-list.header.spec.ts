import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { PortfolioAccountsListComponent } from './accounts-list';

describe('PortfolioAccountsListComponent page header', () => {
  it('offers 新增帳戶 as a soft app-btn in the page header', () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    const fixture = TestBed.createComponent(PortfolioAccountsListComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const create = Array.from(el.querySelectorAll('app-page-header app-btn')).find(btn => btn.textContent?.includes('新增帳戶'));
    expect(create?.querySelector('.btn-soft')).not.toBeNull();
  });
});
