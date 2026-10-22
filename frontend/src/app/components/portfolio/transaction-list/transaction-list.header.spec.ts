import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { PortfolioTransactionListComponent } from './transaction-list';

describe('PortfolioTransactionListComponent page header', () => {
  it('offers 新增交易 as a soft app-btn and has no 記一筆 button', () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const create = Array.from(el.querySelectorAll('app-page-header app-btn')).find(btn => btn.textContent?.includes('新增交易'));
    expect(create?.querySelector('.btn-soft')).not.toBeNull();
    expect(el.textContent).not.toContain('記一筆');
  });
});
