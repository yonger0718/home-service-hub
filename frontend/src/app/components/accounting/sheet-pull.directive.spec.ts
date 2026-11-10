// frontend/src/app/components/accounting/sheet-pull.directive.spec.ts
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SHEET_PULL_CLOSE_PX, SheetPullDirective } from './sheet-pull.directive';

@Component({
  imports: [SheetPullDirective],
  template: `
    <div class="sheet">
      @if (showGrab()) {
        <div class="grab" appSheetPull (pulled)="n = n + 1"></div>
      }
      <h3>測試面板</h3>
    </div>
  `,
})
class HostComponent {
  n = 0;
  readonly showGrab = signal(true);
}

/** jsdom may lack PointerEvent: a MouseEvent with the pointer fields the directive reads. */
function ptr(type: string, clientY: number, extra: { button?: number; pointerId?: number } = {}): Event {
  const ev = new MouseEvent(type, { clientY, button: extra.button ?? 0, bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'pointerId', { value: extra.pointerId ?? 1 });
  Object.defineProperty(ev, 'isPrimary', { value: true });
  Object.defineProperty(ev, 'pointerType', { value: 'touch' });
  return ev;
}

function setup(height = 400): {
  fixture: ComponentFixture<HostComponent>;
  sheet: HTMLElement;
  grab: HTMLElement;
  cmp: HostComponent;
} {
  const fixture = TestBed.createComponent(HostComponent);
  fixture.detectChanges();
  const root = fixture.nativeElement as HTMLElement;
  const sheet = root.querySelector('.sheet') as HTMLElement;
  const grab = root.querySelector('.grab') as HTMLElement;
  vi.spyOn(sheet, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 360,
    bottom: height,
    width: 360,
    height,
    toJSON: () => ({}),
  } as DOMRect);
  return { fixture, sheet, grab, cmp: fixture.componentInstance };
}

/** A full drag on the grab bar: down at 100, then each move, then up at the last position. */
function drag(grab: HTMLElement, ...ys: number[]): void {
  grab.dispatchEvent(ptr('pointerdown', 100));
  for (const y of ys) grab.dispatchEvent(ptr('pointermove', y));
  grab.dispatchEvent(ptr('pointerup', ys.length ? ys[ys.length - 1] : 100));
}

describe('SheetPullDirective', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('a drag of 80 px down emits pulled once and clears the transform', () => {
    const { sheet, grab, cmp } = setup();
    expect(SHEET_PULL_CLOSE_PX).toBe(80);
    drag(grab, 140, 180);
    expect(cmp.n).toBe(1);
    expect(sheet.style.transform).toBe('');
  });

  it('a drag of 79 px springs back without emitting', () => {
    const { sheet, grab, cmp } = setup();
    drag(grab, 140, 179);
    expect(cmp.n).toBe(0);
    expect(sheet.style.transform).toBe('');
    expect(sheet.classList.contains('pulling')).toBe(false);
  });

  it('a short sheet closes at 30 % of its height', () => {
    const { grab, cmp } = setup(200);
    drag(grab, 159);
    expect(cmp.n).toBe(0);
    drag(grab, 160);
    expect(cmp.n).toBe(1);
  });

  it('the sheet follows the finger: translateY(<dy>px) while dragging, never negative', () => {
    const { sheet, grab } = setup();
    grab.dispatchEvent(ptr('pointerdown', 100));
    grab.dispatchEvent(ptr('pointermove', 125));
    expect(sheet.style.transform).toBe('translateY(25px)');
    grab.dispatchEvent(ptr('pointermove', 70));
    expect(sheet.style.transform).toBe('translateY(0px)');
    grab.dispatchEvent(ptr('pointerup', 70));
    expect(sheet.style.transform).toBe('');
  });

  it('pulling class is on the sheet only during the drag', () => {
    const { sheet, grab } = setup();
    expect(sheet.classList.contains('pulling')).toBe(false);
    grab.dispatchEvent(ptr('pointerdown', 100));
    expect(sheet.classList.contains('pulling')).toBe(true);
    grab.dispatchEvent(ptr('pointermove', 130));
    expect(sheet.classList.contains('pulling')).toBe(true);
    grab.dispatchEvent(ptr('pointerup', 130));
    expect(sheet.classList.contains('pulling')).toBe(false);
  });

  it('pointercancel, lostpointercapture and Escape end the drag without emitting', () => {
    const { sheet, grab, cmp } = setup();
    const ends: Array<() => void> = [
      () => grab.dispatchEvent(ptr('pointercancel', 200)),
      () => grab.dispatchEvent(ptr('lostpointercapture', 200)),
      () => grab.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })),
    ];
    for (const end of ends) {
      grab.dispatchEvent(ptr('pointerdown', 100));
      grab.dispatchEvent(ptr('pointermove', 200));
      expect(sheet.style.transform).toBe('translateY(100px)');
      end();
      expect(sheet.style.transform).toBe('');
      expect(sheet.classList.contains('pulling')).toBe(false);
      // The drag is over: a later release past the threshold emits nothing.
      grab.dispatchEvent(ptr('pointerup', 200));
      expect(cmp.n).toBe(0);
    }
  });

  it('Escape during a drag is not stopped', () => {
    const { sheet, grab } = setup();
    const seen: KeyboardEvent[] = [];
    sheet.addEventListener('keydown', e => seen.push(e));
    grab.dispatchEvent(ptr('pointerdown', 100));
    grab.dispatchEvent(ptr('pointermove', 150));
    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    grab.dispatchEvent(esc);
    expect(seen).toEqual([esc]);
    expect(esc.defaultPrevented).toBe(false);
    expect(sheet.style.transform).toBe('');
  });

  it('pointerup at 80 px followed by lostpointercapture emits once', () => {
    const { sheet, grab, cmp } = setup();
    grab.dispatchEvent(ptr('pointerdown', 100));
    grab.dispatchEvent(ptr('pointermove', 180));
    grab.dispatchEvent(ptr('pointerup', 180));
    grab.dispatchEvent(ptr('lostpointercapture', 180));
    expect(cmp.n).toBe(1);
    expect(sheet.style.transform).toBe('');
  });

  it('spring-back order: pulling is removed before the inline transform is cleared', () => {
    const { sheet, grab } = setup();
    grab.dispatchEvent(ptr('pointerdown', 100));
    grab.dispatchEvent(ptr('pointermove', 140));
    const log: string[] = [];
    const observer = new MutationObserver(() => undefined);
    observer.observe(sheet, { attributes: true, attributeFilter: ['class', 'style'] });
    // The forced reflow sits between the two: pulling already gone, transform still in place.
    vi.spyOn(sheet, 'offsetHeight', 'get').mockImplementation(() => {
      log.push(`reflow pulling=${sheet.classList.contains('pulling')} transform=${sheet.style.transform}`);
      return 400;
    });
    grab.dispatchEvent(ptr('pointerup', 140));
    for (const r of observer.takeRecords()) {
      log.push(r.attributeName === 'class' ? `class ${sheet.className}` : 'style');
    }
    observer.disconnect();
    expect(log[0]).toBe('reflow pulling=false transform=translateY(40px)');
    expect(log.slice(1)).toEqual(['class sheet', 'style']);
  });

  it('a secondary mouse button does not start a drag', () => {
    const { sheet, grab, cmp } = setup();
    grab.dispatchEvent(ptr('pointerdown', 100, { button: 2 }));
    expect(sheet.classList.contains('pulling')).toBe(false);
    grab.dispatchEvent(ptr('pointermove', 200, { button: 2 }));
    expect(sheet.style.transform).toBe('');
    grab.dispatchEvent(ptr('pointerup', 200, { button: 2 }));
    expect(cmp.n).toBe(0);
  });

  it('destroying the host mid-drag emits nothing', () => {
    const { fixture, sheet, grab, cmp } = setup();
    grab.dispatchEvent(ptr('pointerdown', 100));
    grab.dispatchEvent(ptr('pointermove', 200));
    cmp.showGrab.set(false);
    fixture.detectChanges();
    expect(grab.isConnected).toBe(false);
    expect(sheet.classList.contains('pulling')).toBe(false);
    expect(sheet.style.transform).toBe('');
    grab.dispatchEvent(ptr('pointermove', 260));
    grab.dispatchEvent(ptr('pointerup', 260));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(cmp.n).toBe(0);
    expect(sheet.style.transform).toBe('');
  });
});
