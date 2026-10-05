import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { COMPACT_QUERY, MATCH_MEDIA, PANES_QUERY } from '../../services/layout-mode.service';
import { ShellComponent } from './shell';

/** A 1280 px wide viewport whose height the test controls. */
function fakeViewport(initialHeight: number) {
  let height = initialHeight;
  const listeners = new Set<() => void>();
  const matchMedia = (query: string) =>
    ({
      media: query,
      get matches() {
        if (query === COMPACT_QUERY) {
          return height <= 820;
        }
        return query === PANES_QUERY;
      },
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    }) as unknown as MediaQueryList;
  return {
    matchMedia,
    setHeight(next: number) {
      height = next;
      listeners.forEach(listener => listener());
    },
  };
}

function render(height: number) {
  const viewport = fakeViewport(height);
  TestBed.configureTestingModule({
    imports: [ShellComponent],
    providers: [provideRouter([]), { provide: MATCH_MEDIA, useValue: viewport.matchMedia }],
  });
  const fixture = TestBed.createComponent(ShellComponent);
  fixture.detectChanges();
  return { fixture, viewport, root: () => (fixture.nativeElement as HTMLElement).querySelector('.hub-layout')! };
}

describe('ShellComponent', () => {
  it('marks the layout compact-height on a short (800 px) screen', () => {
    const { root } = render(800);

    expect(root().classList.contains('compact-height')).toBe(true);
  });

  it('leaves the layout at full density on a tall (1080 px) screen', () => {
    const { root } = render(1080);

    expect(root().classList.contains('compact-height')).toBe(false);
  });

  it('toggles compact-height as the viewport height crosses 820 px', () => {
    const { fixture, viewport, root } = render(1080);

    viewport.setHeight(800);
    fixture.detectChanges();
    expect(root().classList.contains('compact-height')).toBe(true);

    viewport.setHeight(900);
    fixture.detectChanges();
    expect(root().classList.contains('compact-height')).toBe(false);
  });
});
