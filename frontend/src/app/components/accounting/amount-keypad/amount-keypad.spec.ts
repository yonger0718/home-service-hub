import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { AmountKeypadComponent } from './amount-keypad';

function render(inputs: Record<string, unknown> = {}) {
  TestBed.configureTestingModule({ imports: [AmountKeypadComponent] });
  const fixture = TestBed.createComponent(AmountKeypadComponent);
  for (const [name, value] of Object.entries(inputs)) {
    fixture.componentRef.setInput(name, value);
  }
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  const press = (...keys: string[]) => {
    for (const key of keys) {
      (el.querySelector(`button[data-key="${key}"]`) as HTMLButtonElement).click();
      fixture.detectChanges();
    }
  };
  return { fixture, el, press, component: fixture.componentInstance };
}

describe('AmountKeypadComponent', () => {
  it('saves 120 after 3 × 4 0 ✓', () => {
    const { press, component } = render();
    const saved: (number | null)[] = [];
    component.save.subscribe(value => saved.push(value));

    press('3', '×', '4', '0', '✓');

    expect(saved).toEqual([120]);
    expect(component.value()).toBe('120');
  });

  it('renders the calculator layout by default and the phone layout on request', () => {
    const calculator = render().el;
    expect(Array.from(calculator.querySelectorAll('.keypad button')).slice(4, 8).map(b => b.textContent?.trim())).toEqual(['7', '8', '9', '⌫']);

    TestBed.resetTestingModule();
    const phone = render({ layout: 'phone' }).el;
    expect(Array.from(phone.querySelectorAll('.keypad button')).slice(4, 8).map(b => b.textContent?.trim())).toEqual(['1', '2', '3', '⌫']);
  });

  it('emits next on ↵ with the evaluated amount and clears with C', () => {
    const { press, component } = render();
    const nexts: (number | null)[] = [];
    component.next.subscribe(value => nexts.push(value));

    press('1', '2', '+', '8', '↵');
    expect(nexts).toEqual([20]);

    press('C');
    expect(component.value()).toBe('');
  });

  it('shows an error and does not save on division by zero', () => {
    const { press, component, el } = render();
    const saved: (number | null)[] = [];
    component.save.subscribe(value => saved.push(value));

    press('1', '÷', '0', '✓');

    expect(saved).toEqual([]);
    expect(el.querySelector('.keypad-error')?.textContent?.trim()).toBe('算式有誤：除以零');
  });

  it('explains an unparsable expression in Chinese', () => {
    const { component, el, fixture } = render();
    component.value.set('1÷÷2');
    fixture.detectChanges();
    (el.querySelector('button[data-key="✓"]') as HTMLButtonElement).click();
    fixture.detectChanges();

    expect(el.querySelector('.keypad-error')?.textContent?.trim()).toBe('算式有誤：無法解析');
  });

  it('replaces the value with a quick amount', () => {
    const { el, fixture, component } = render({ quickAmounts: [120, 1500] });
    component.value.set('3×');

    const chips = Array.from(el.querySelectorAll('.quick button')) as HTMLButtonElement[];
    expect(chips.map(c => c.textContent?.trim())).toEqual(['120', '1,500']);
    chips[1].click();
    fixture.detectChanges();

    expect(component.value()).toBe('1500');
  });

  it('rounds to the given decimals', () => {
    const { press, component } = render({ decimals: 2 });
    press('1', '0', '0', '÷', '3', '✓');
    expect(component.value()).toBe('33.33');
  });
});
