import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { LogoLockupComponent } from './logo-lockup';

describe('LogoLockupComponent', () => {
  it('renders the lockup inline with token fills only', async () => {
    const fixture = TestBed.createComponent(LogoLockupComponent);
    fixture.detectChanges();
    const svg = fixture.nativeElement.querySelector('svg[role="img"]')!;
    expect(svg.getAttribute('aria-label')).toBe('Home Hub');
    expect(svg.textContent).toContain('Home Hub');
    expect(svg.textContent).toContain('家庭服務中樞');
    expect(svg.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b/i); // no hard-coded colour
    expect(svg.querySelector('text')!.getAttribute('fill')).toBe('currentColor');
  });
});
