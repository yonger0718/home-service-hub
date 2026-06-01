import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { ShoppingListComponent } from './shopping-list';

describe('ShoppingListComponent', () => {
  it('renders the labelled empty state', async () => {
    await TestBed.configureTestingModule({
      imports: [ShoppingListComponent],
    }).compileComponents();

    const fixture = TestBed.createComponent(ShoppingListComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('採買清單');
    expect(fixture.nativeElement.textContent).toContain('尚未設計完成');
    expect(fixture.nativeElement.querySelector('.pi-shopping-cart')).toBeTruthy();
  });
});
