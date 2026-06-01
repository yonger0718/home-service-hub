import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

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
});
