import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { AmountFieldComponent, groupAmount, stripAmount } from './amount-field';

describe('stripAmount', () => {
  it('removes commas and whitespace only', () => {
    expect(stripAmount('200,000')).toBe('200000');
    expect(stripAmount('200000')).toBe('200000');
    expect(stripAmount(' 200000 ')).toBe('200000');
    expect(stripAmount('')).toBe('');
    expect(stripAmount('abc')).toBe('abc');
  });
});

describe('groupAmount', () => {
  it('groups integer digits by string operations', () => {
    expect(groupAmount('9007199254740993')).toBe('9,007,199,254,740,993');
    expect(groupAmount('-1234567890123.45')).toBe('-1,234,567,890,123.45');
    expect(groupAmount('200000')).toBe('200,000');
    expect(groupAmount('123')).toBe('123');
  });

  it('keeps the fractional part verbatim and leaves non-numeric text alone', () => {
    expect(groupAmount('1.500')).toBe('1.500');
    expect(groupAmount('')).toBe('');
    expect(groupAmount('abc')).toBe('abc');
  });
});

@Component({
  imports: [AmountFieldComponent],
  template: `<app-amount-field
    [value]="value()"
    prefix="NT$"
    suffix="%"
    ariaLabel="信用額度"
    [invalid]="invalid()"
    (valueChange)="emitted.push($event)"
  ></app-amount-field>`,
})
class AmountFieldHostComponent {
  value = signal('200000');
  invalid = signal(false);
  emitted: string[] = [];
}

function setup() {
  const fixture = TestBed.createComponent(AmountFieldHostComponent);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  const input = el.querySelector('input.amt') as HTMLInputElement;
  return { fixture, host: fixture.componentInstance, el, input };
}

describe('AmountFieldComponent', () => {
  it('shows prefix and grouped value when unfocused', () => {
    const { el, input } = setup();
    expect(input.value).toBe('200,000');
    expect(input.getAttribute('inputmode')).toBe('decimal');
    expect(input.getAttribute('aria-label')).toBe('信用額度');
    expect(el.textContent).toContain('NT$');
    expect(el.textContent).toContain('%');
  });

  it('shows raw value while focused', () => {
    const { fixture, input } = setup();
    input.dispatchEvent(new Event('focus'));
    fixture.detectChanges();
    expect(input.value).toBe('200000');
    input.dispatchEvent(new Event('blur'));
    fixture.detectChanges();
    expect(input.value).toBe('200,000');
  });

  it("typing 200,000 emits '200000'", () => {
    const { host, input } = setup();
    input.value = '200,000';
    input.dispatchEvent(new Event('input'));
    expect(host.emitted).toEqual(['200000']);
  });

  it('sets aria-invalid when invalid', () => {
    const { fixture, host, input } = setup();
    expect(input.getAttribute('aria-invalid')).not.toBe('true');
    host.invalid.set(true);
    fixture.detectChanges();
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });
});
