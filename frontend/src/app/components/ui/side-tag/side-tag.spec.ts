import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { SideTagComponent } from './side-tag';

@Component({
  imports: [SideTagComponent],
  template: `<app-side-tag variant="buy"></app-side-tag><app-side-tag variant="cash" label="股"></app-side-tag>`,
})
class SideTagHostComponent {}

describe('SideTagComponent', () => {
  it('renders default and custom labels with variant classes', () => {
    const fixture = TestBed.createComponent(SideTagHostComponent);
    fixture.detectChanges();

    const tags = fixture.nativeElement.querySelectorAll('.side-tag') as NodeListOf<HTMLElement>;
    expect(tags[0].classList.contains('buy')).toBe(true);
    expect(tags[0].textContent).toContain('買');
    expect(tags[1].classList.contains('cash')).toBe(true);
    expect(tags[1].textContent).toContain('股');
  });
});
