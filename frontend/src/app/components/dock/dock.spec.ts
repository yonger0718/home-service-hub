import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { COMPACT_QUERY, MATCH_MEDIA } from '../../services/layout-mode.service';
import { DockComponent } from './dock';

function render(height: number): HTMLElement {
  const matchMedia = (query: string) =>
    ({
      media: query,
      matches: query === COMPACT_QUERY ? height <= 820 : false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
  TestBed.configureTestingModule({
    imports: [DockComponent],
    providers: [provideRouter([]), { provide: MATCH_MEDIA, useValue: matchMedia }],
  });
  const fixture = TestBed.createComponent(DockComponent);
  fixture.componentRef.setInput('activeId', 'inventory');
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('DockComponent', () => {
  it('marks the host compact on a short (800 px) screen', () => {
    expect(render(800).classList.contains('compact')).toBe(true);
  });

  it('keeps the host at full density on a tall (1080 px) screen', () => {
    expect(render(1080).classList.contains('compact')).toBe(false);
  });

  it('keeps the logo and the nav inside the dock, which is its own scroll container', () => {
    const host = render(800);

    // The overflow-y rule lives in dock.scss (jsdom does not apply it); the structure it relies on is checked here.
    const dock = host.querySelector('aside.hub-dock')!;
    expect(Array.from(dock.children).map(child => child.className)).toEqual(['dock-logo', 'dock-nav']);
    expect(dock.querySelector('nav.dock-nav')!.querySelectorAll('a.dock-item').length).toBeGreaterThan(0);
  });
});
