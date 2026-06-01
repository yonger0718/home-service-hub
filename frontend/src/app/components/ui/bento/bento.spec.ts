import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { BentoComponent } from './bento';

@Component({
  imports: [BentoComponent],
  template: `<app-bento title="Overview" [full]="true"><p>content</p></app-bento>`,
})
class BentoHostComponent {}

describe('BentoComponent', () => {
  it('renders title, projected content, and full variant class', () => {
    const fixture = TestBed.createComponent(BentoHostComponent);
    fixture.detectChanges();

    const bento = fixture.nativeElement.querySelector('.bento') as HTMLElement;
    expect(bento.classList.contains('b-full')).toBe(true);
    expect(bento.querySelector('.card-title')?.textContent).toContain('Overview');
    expect(bento.textContent).toContain('content');
  });
});
