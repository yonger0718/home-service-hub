import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { SegToggleComponent, SegToggleOption } from './seg-toggle';

@Component({
  imports: [SegToggleComponent],
  template: `<app-seg-toggle [options]="options" [value]="value" ariaLabel="Range" (change)="onChange($event)"></app-seg-toggle>`,
})
class SegToggleHostComponent {
  value = 'one';
  emitted: string[] = [];
  options: SegToggleOption[] = [
    { value: 'one', label: 'One' },
    { value: 'two', label: 'Two' },
    { value: 'three', label: 'Three' },
  ];

  onChange(value: string) {
    this.value = value;
    this.emitted.push(value);
  }
}

@Component({
  imports: [SegToggleComponent],
  template: `<app-seg-toggle [block]="true" [options]="options" value="one"></app-seg-toggle>`,
})
class BlockHostComponent {
  options: SegToggleOption[] = [{ value: 'one', label: 'One' }, { value: 'two', label: 'Two' }];
}

describe('SegToggleComponent', () => {
  it('renders aria-checked and emits selected option on click', () => {
    const fixture = TestBed.createComponent(SegToggleHostComponent);
    fixture.detectChanges();

    const buttons = fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>;
    expect(buttons[0].getAttribute('aria-checked')).toBe('true');
    expect(buttons[1].getAttribute('aria-checked')).toBe('false');

    buttons[1].click();
    fixture.detectChanges();

    expect(fixture.componentInstance.emitted).toEqual(['two']);
    expect(buttons[1].getAttribute('aria-checked')).toBe('true');
  });

  it('supports arrow-key navigation', () => {
    const fixture = TestBed.createComponent(SegToggleHostComponent);
    fixture.detectChanges();

    const buttons = fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>;
    buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    fixture.detectChanges();

    expect(fixture.componentInstance.emitted).toEqual(['two']);
    expect(buttons[1].getAttribute('aria-checked')).toBe('true');
  });

  it('block stretches the host, the group and its buttons', () => {
    const fixture = TestBed.createComponent(BlockHostComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('app-seg-toggle').classList).toContain('block');

    const css = readFileSync(join(process.cwd(), 'src/app/components/ui/seg-toggle/seg-toggle.scss'), 'utf8');
    const rule = (selector: string) => {
      const start = css.indexOf(selector + ' {');
      expect(start, selector).toBeGreaterThanOrEqual(0);
      return css.slice(start, css.indexOf('}', start));
    };
    expect(rule(':host(.block)')).toMatch(/display:\s*block/);
    expect(rule(':host(.block)')).toMatch(/width:\s*100%/);
    expect(rule(':host(.block) .seg-toggle')).toMatch(/display:\s*flex/);
    expect(rule(':host(.block) .seg-toggle')).toMatch(/width:\s*100%/);
    expect(rule(':host(.block) .seg-toggle button')).toMatch(/flex:\s*1 1 0/);
  });
});
