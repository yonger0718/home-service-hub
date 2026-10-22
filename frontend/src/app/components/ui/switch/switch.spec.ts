import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { SwitchComponent } from './switch';

@Component({
  imports: [SwitchComponent],
  template: `<app-switch [checked]="checked()" [disabled]="disabled()" ariaLabel="Dark mode" (checkedChange)="emitted.push($event)"></app-switch>`,
})
class SwitchHostComponent {
  checked = signal(false);
  disabled = signal(false);
  emitted: boolean[] = [];
}

function setup() {
  const fixture = TestBed.createComponent(SwitchHostComponent);
  fixture.detectChanges();
  const button = () => fixture.nativeElement.querySelector('button') as HTMLButtonElement;
  return { fixture, host: fixture.componentInstance, button };
}

describe('SwitchComponent', () => {
  it('renders a switch button mirroring checked and label', () => {
    const { fixture, host, button } = setup();
    expect(button().getAttribute('role')).toBe('switch');
    expect(button().getAttribute('aria-label')).toBe('Dark mode');
    expect(button().getAttribute('aria-checked')).toBe('false');
    host.checked.set(true);
    fixture.detectChanges();
    expect(button().getAttribute('aria-checked')).toBe('true');
  });

  it('emits the negation once per click', () => {
    const { fixture, host, button } = setup();
    button().click();
    expect(host.emitted).toEqual([true]);
    host.checked.set(true);
    fixture.detectChanges();
    button().click();
    expect(host.emitted).toEqual([true, false]);
  });

  it('does not emit when disabled', () => {
    const { fixture, host, button } = setup();
    host.disabled.set(true);
    fixture.detectChanges();
    expect(button().disabled).toBe(true);
    button().click();
    expect(host.emitted).toEqual([]);
  });
});
