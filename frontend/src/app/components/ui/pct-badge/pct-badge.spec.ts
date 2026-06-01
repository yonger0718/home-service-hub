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
    expect(badge.textContent).toContain('+4.20%');
    expect(badge.classList.contains('down')).toBe(false);
  });

  it('marks negative percentages as down', () => {
    const fixture = TestBed.createComponent(PctBadgeHostComponent);
    fixture.componentInstance.value = -1.2;
    fixture.detectChanges();

    const badge = fixture.nativeElement.querySelector('.pct-badge') as HTMLElement;
    expect(badge.textContent).toContain('-1.20%');
    expect(badge.classList.contains('down')).toBe(true);
  });
});
