import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { makeAccount } from '../testing/fixtures';
import { FxSheetComponent, FxValue } from './fx-sheet';

const START: FxValue = {
  original_amount: '5390',
  original_currency: 'JPY',
  fx_rate: null,
  amount: null,
  use_online: true,
  manual: null,
  rate_date: null,
};

describe('FxSheetComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [FxSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  function render(value: FxValue | null = START) {
    const fixture = TestBed.createComponent(FxSheetComponent);
    fixture.componentRef.setInput('account', makeAccount({ id: 7, name: '富邦 J卡', currency: 'TWD' }));
    fixture.componentRef.setInput('entryDate', '2026-10-02');
    fixture.componentRef.setInput('value', value);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const type = (selector: string, text: string) => {
      const input = el.querySelector(selector) as HTMLInputElement;
      input.value = text;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    };
    return { fixture, el, type, component: fixture.componentInstance };
  }

  function flushRate(rate = '0.2163000000'): void {
    const req = httpMock.expectOne(r => r.url === '/api/accounting/fx-rate');
    expect(req.request.params.get('date')).toBe('2026-10-02');
    expect(req.request.params.get('base')).toBe('JPY');
    expect(req.request.params.get('quote')).toBe('TWD');
    req.flush({ date: '2026-10-02', base: 'JPY', quote: 'TWD', rate, source: 'cache' });
  }

  it('proposes the online rate and the converted amount', () => {
    const { fixture, el } = render();
    flushRate();
    fixture.detectChanges();

    expect((el.querySelector('.fx-rate') as HTMLInputElement).value).toBe('0.2163');
    expect((el.querySelector('.fx-converted') as HTMLInputElement).value).toBe('1,166');
    expect(el.querySelector('.tog')?.classList).toContain('on');
    expect(el.textContent).toContain('2026/10/02 線上匯率');
    expect(el.textContent).toContain('轉換成 TWD');
  });

  it('turns the online toggle off when the converted amount is edited', () => {
    const { fixture, el, type, component } = render();
    flushRate();
    fixture.detectChanges();
    let closed = 0;
    component.closed.subscribe(() => closed++);

    type('.fx-converted', '1170');
    expect(el.querySelector('.tog')?.classList).not.toContain('on');
    (el.querySelector('.fx-confirm') as HTMLButtonElement).click();

    expect(component.value()).toEqual({
      original_amount: '5390',
      original_currency: 'JPY',
      fx_rate: '0.2163',
      amount: '1170',
      use_online: false,
      manual: 'amount',
      rate_date: '2026-10-02',
    });
    expect(closed).toBe(1);
  });

  it('recomputes from an edited rate and refetches when the toggle is switched back on', () => {
    const { fixture, el, type } = render();
    flushRate();
    fixture.detectChanges();

    type('.fx-rate', '0.22');
    expect((el.querySelector('.fx-converted') as HTMLInputElement).value).toBe('1,186');
    expect(el.querySelector('.tog')?.classList).not.toContain('on');

    (el.querySelector('.tog') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushRate('0.2170000000');
    fixture.detectChanges();
    expect((el.querySelector('.fx-rate') as HTMLInputElement).value).toBe('0.217');
    expect(el.querySelector('.tog')?.classList).toContain('on');
  });

  it('clears the conversion when the account currency is chosen again', () => {
    const { fixture, el, component } = render();
    flushRate();
    fixture.detectChanges();

    const select = el.querySelector('.fx-currency') as HTMLSelectElement;
    select.value = 'TWD';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(el.querySelector('.fx-rate')).toBeNull();
    (el.querySelector('.fx-confirm') as HTMLButtonElement).click();

    expect(component.value()).toBeNull();
  });
});
