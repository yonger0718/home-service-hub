import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { TagComponent } from './tag';

@Component({
  imports: [TagComponent],
  template: `<app-tag variant="dividend" icon="pi-percentage">股利</app-tag>`,
})
class TagHostComponent {}

describe('TagComponent', () => {
  it('renders projected text with variant and icon classes', () => {
    const fixture = TestBed.createComponent(TagHostComponent);
    fixture.detectChanges();

    const tag = fixture.nativeElement.querySelector('.tag') as HTMLElement;
    expect(tag.classList.contains('tag-dividend')).toBe(true);
    expect(tag.querySelector('.pi-percentage')).toBeTruthy();
    expect(tag.textContent).toContain('股利');
  });
});
