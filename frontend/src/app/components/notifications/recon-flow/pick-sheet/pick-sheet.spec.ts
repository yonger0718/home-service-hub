import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppFlowPickSheetComponent, FlowPickOption } from './pick-sheet';

const OPTIONS: FlowPickOption[] = [
  { id: 11, label: '範例帳戶甲' },
  { id: 12, label: '範例帳戶乙' },
];

describe('AppFlowPickSheetComponent', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [AppFlowPickSheetComponent] }));

  function render(options: readonly FlowPickOption[] = OPTIONS, sub: string | null = '請選一個') {
    const fixture = TestBed.createComponent(AppFlowPickSheetComponent);
    fixture.componentRef.setInput('heading', '轉到哪個帳戶');
    fixture.componentRef.setInput('sub', sub);
    fixture.componentRef.setInput('options', options);
    const picked = vi.fn<(id: number) => void>();
    const closed = vi.fn();
    fixture.componentInstance.picked.subscribe(picked);
    fixture.componentInstance.closed.subscribe(closed);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el, picked, closed };
  }

  const confirmBtn = (el: HTMLElement) => el.querySelector<HTMLButtonElement>('.sheet-confirm')!;
  const radios = (el: HTMLElement) => Array.from(el.querySelectorAll<HTMLInputElement>('input[type="radio"]'));

  it('renders the heading, sub line and one radio row per option', () => {
    const { el } = render();
    const sheet = el.querySelector('.overlay[data-overlay] > .sheet[role="dialog"][aria-modal="true"]')!;
    const h3 = sheet.querySelector('h3')!;
    expect(h3.textContent?.trim()).toBe('轉到哪個帳戶');
    expect(sheet.getAttribute('aria-labelledby')).toBe(h3.id);
    expect(el.querySelector('.pick-sub')?.textContent?.trim()).toBe('請選一個');
    expect(el.querySelector('[role="radiogroup"]')).not.toBeNull();
    expect(Array.from(el.querySelectorAll('.pick-row')).map((r) => r.textContent?.trim())).toEqual([
      '範例帳戶甲',
      '範例帳戶乙',
    ]);
  });

  it('omits the sub line when null', () => {
    expect(render(OPTIONS, null).el.querySelector('.pick-sub')).toBeNull();
  });

  it('keeps 確定 disabled until a row is picked, then emits its id', () => {
    const { fixture, el, picked } = render();
    expect(confirmBtn(el).disabled).toBe(true);
    radios(el)[1].click();
    fixture.detectChanges();
    expect(confirmBtn(el).disabled).toBe(false);
    confirmBtn(el).click();
    expect(picked).toHaveBeenCalledWith(12);
  });

  it('pre-picks a single option, and starts over when the options change', () => {
    const { fixture, el, picked } = render([OPTIONS[0]]);
    expect(radios(el)[0].checked).toBe(true);
    confirmBtn(el).click();
    expect(picked).toHaveBeenCalledWith(11);

    fixture.componentRef.setInput('options', OPTIONS);
    fixture.detectChanges();
    expect(radios(el).some((r) => r.checked)).toBe(false);
    expect(confirmBtn(el).disabled).toBe(true);
  });

  it('Esc closes the sheet (marked handled), and 取消 and a backdrop click emit closed', () => {
    const { el, closed } = render();
    document.body.appendChild(el);
    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.sheet')!.dispatchEvent(esc);
    expect(closed).toHaveBeenCalledTimes(1);
    expect(esc.defaultPrevented).toBe(true);
    el.remove();

    el.querySelector<HTMLButtonElement>('.sheet-cancel')!.click();
    expect(closed).toHaveBeenCalledTimes(2);
    el.querySelector<HTMLElement>('.overlay')!.click();
    expect(closed).toHaveBeenCalledTimes(3);
  });

  it('Tab at the last control wraps inside the sheet', () => {
    const { fixture, el } = render();
    document.body.appendChild(el);
    fixture.detectChanges();
    radios(el)[0].click();
    fixture.detectChanges();
    confirmBtn(el).focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    confirmBtn(el).dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);
    el.remove();
  });
});
