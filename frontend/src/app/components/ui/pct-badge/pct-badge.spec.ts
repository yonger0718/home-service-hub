import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { PctBadgeComponent } from './pct-badge';

@Component({
  imports: [PctBadgeComponent],
  template: `<app-pct-badge [value]="value"></app-pct-badge>`,
})
class PctBadgeHostComponent {
  value = 4.2;
}

describe('PctBadgeComponent', () => {
  it('renders signed positive percentage', () => {
    const fixture = TestBed.createComponent(PctBadgeHostComponent);
    fixture.detectChanges();

    const badge = fixture.nativeElement.querySelector('.pct-badge') as HTMLElement;
    expect(badge.textContent).toContain('+4.2%');
    expect(badge.classList.contains('down')).toBe(false);
  });

  it('marks negative percentages as down', () => {
    const fixture = TestBed.createComponent(PctBadgeHostComponent);
    fixture.componentInstance.value = -1.2;
    fixture.detectChanges();

    const badge = fixture.nativeElement.querySelector('.pct-badge') as HTMLElement;
    expect(badge.textContent).toContain('−1.2%');
    expect(badge.classList.contains('down')).toBe(true);
  });

  it('shows a value that rounds to zero as neutral, not down', () => {
    const fixture = TestBed.createComponent(PctBadgeHostComponent);
    fixture.componentInstance.value = -0.04;
    fixture.detectChanges();

    const badge = fixture.nativeElement.querySelector('.pct-badge') as HTMLElement;
    expect(badge.textContent).toContain('0.0%');
    expect(badge.classList.contains('neutral')).toBe(true);
    expect(badge.classList.contains('down')).toBe(false);
  });
});
