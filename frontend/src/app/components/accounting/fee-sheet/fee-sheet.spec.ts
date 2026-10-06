import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { ChildInput } from '../../../models/accounting.model';
import { FeeSheetComponent } from './fee-sheet';

function render(fee: ChildInput | null = null) {
  TestBed.configureTestingModule({ imports: [FeeSheetComponent] });
  const fixture = TestBed.createComponent(FeeSheetComponent);
  fixture.componentRef.setInput('amount', -1166);
  fixture.componentRef.setInput('currency', 'TWD');
  fixture.componentRef.setInput('kind', 'expense');
  fixture.componentRef.setInput('fee', fee);
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

describe('FeeSheetComponent', () => {
  it('adds a renamed fee and shows the total', () => {
    const { fixture, el, type, component } = render();

    expect(el.querySelector('h3')?.textContent).toBe('支出');
    expect(el.querySelector('.fee-base')?.textContent?.trim()).toBe('−1,166');
    type('.fee-amount', '17');
    expect(el.querySelector('.fee-total')?.textContent?.trim()).toBe('−1,183');
    (el.querySelector('.fee-rename') as HTMLButtonElement).click();
    fixture.detectChanges();
    type('.fee-name', '國外交易手續費');
    (el.querySelector('.sheet-confirm') as HTMLButtonElement).click();

    expect(component.fee()).toEqual({ amount: '17', name: '國外交易手續費' });
    expect(component.discount()).toBeNull();
  });

  it('removes a fee set to zero and keeps a discount', () => {
    const { type, el, component } = render({ amount: '15', name: null });

    expect((el.querySelector('.fee-amount') as HTMLInputElement).value).toBe('15');
    type('.fee-amount', '0');
    type('.discount-amount', '50');
    expect(el.querySelector('.fee-total')?.textContent?.trim()).toBe('−1,116');
    (el.querySelector('.sheet-confirm') as HTMLButtonElement).click();

    expect(component.fee()).toBeNull();
    expect(component.discount()).toEqual({ amount: '50', name: null });
  });
  it('closes on Esc as a handled key and keeps Tab inside the sheet', () => {
    const { fixture, el, component } = render();
    const closed = vi.fn();
    component.closed.subscribe(closed);
    expect(el.querySelector('.overlay')!.hasAttribute('data-overlay')).toBe(true);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);

    const confirm = el.querySelector<HTMLButtonElement>('.sheet-confirm')!;
    confirm.focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    confirm.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.fee-amount')!.dispatchEvent(escape);
    fixture.detectChanges();
    expect(escape.defaultPrevented).toBe(true);
    expect(closed).toHaveBeenCalledTimes(1);
  });

});
