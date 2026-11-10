// frontend/src/app/components/accounting/swipe-x.directive.spec.ts
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SwipeXDirective } from './swipe-x.directive';

@Component({
  imports: [SwipeXDirective],
  template: `
    <div appSwipeX (swipeX)="got.push($event)" style="width:300px;height:200px">
      <input class="field" />
      <button class="btn" (click)="clicks = clicks + 1">go</button>
    </div>
  `,
})
class HostComponent {
  got: Array<1 | -1> = [];
  clicks = 0;
}

function ptr(type: string, x: number, y: number, extra: { pointerId?: number; isPrimary?: boolean } = {}): Event {
  const ev = new MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'pointerId', { value: extra.pointerId ?? 1 });
  Object.defineProperty(ev, 'isPrimary', { value: extra.isPrimary ?? true });
  return ev;
}

function setup() {
  const fixture = TestBed.createComponent(HostComponent);
  fixture.detectChanges();
  const root = fixture.nativeElement as HTMLElement;
  const host = root.querySelector('[appSwipeX]') as HTMLElement;
  const btn = root.querySelector('.btn') as HTMLButtonElement;
  const input = root.querySelector('.field') as HTMLInputElement;
  return { fixture, host, btn, input, cmp: fixture.componentInstance };
}

describe('SwipeXDirective', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('emits 1 for a right-to-left swipe', () => {
    const { host, cmp } = setup();
    host.dispatchEvent(ptr('pointerdown', 200, 100));
    host.dispatchEvent(ptr('pointerup', 120, 110));
    expect(cmp.got).toEqual([1]);
  });

  it('emits -1 for a left-to-right swipe', () => {
    const { host, cmp } = setup();
    host.dispatchEvent(ptr('pointerdown', 100, 100));
    host.dispatchEvent(ptr('pointerup', 170, 96));
    expect(cmp.got).toEqual([-1]);
  });

  it('ignores a short move', () => {
    const { host, cmp } = setup();
    host.dispatchEvent(ptr('pointerdown', 100, 100));
    host.dispatchEvent(ptr('pointerup', 140, 100));
    expect(cmp.got).toEqual([]);
  });

  it('ignores a drag whose vertical movement is not dominated (scroll)', () => {
    const { host, cmp } = setup();
    host.dispatchEvent(ptr('pointerdown', 100, 100));
    host.dispatchEvent(ptr('pointerup', 160, 150));
    expect(cmp.got).toEqual([]);
  });

  it('ignores a gesture that started on a text field', () => {
    const { host, input, cmp } = setup();
    input.dispatchEvent(ptr('pointerdown', 200, 100));
    host.dispatchEvent(ptr('pointerup', 100, 100));
    expect(cmp.got).toEqual([]);
  });

  it('ignores a pointer id that did not start the gesture, and resets on pointercancel', () => {
    const { host, cmp } = setup();
    host.dispatchEvent(ptr('pointerdown', 200, 100, { pointerId: 1 }));
    host.dispatchEvent(ptr('pointerup', 100, 100, { pointerId: 2 }));
    expect(cmp.got).toEqual([]);
    host.dispatchEvent(ptr('pointercancel', 200, 100, { pointerId: 1 }));
    host.dispatchEvent(ptr('pointerup', 100, 100, { pointerId: 1 }));
    expect(cmp.got).toEqual([]);
  });

  it('ignores a non-primary pointer and a non-zero button', () => {
    const { host, cmp } = setup();
    host.dispatchEvent(ptr('pointerdown', 200, 100, { isPrimary: false }));
    host.dispatchEvent(ptr('pointerup', 100, 100));
    const right = new MouseEvent('pointerdown', { clientX: 200, clientY: 100, button: 2, bubbles: true });
    Object.defineProperty(right, 'pointerId', { value: 1 });
    Object.defineProperty(right, 'isPrimary', { value: true });
    host.dispatchEvent(right);
    host.dispatchEvent(ptr('pointerup', 100, 100));
    expect(cmp.got).toEqual([]);
  });

  it('ignores a gesture that ends with a text selection', () => {
    const { host, cmp } = setup();
    vi.spyOn(document, 'getSelection').mockReturnValue({ isCollapsed: false } as Selection);
    host.dispatchEvent(ptr('pointerdown', 200, 100));
    host.dispatchEvent(ptr('pointerup', 120, 100));
    expect(cmp.got).toEqual([]);
  });

  it('swallows the click that follows a swipe on a child button (Review Focus 6)', () => {
    const { btn, cmp } = setup();
    btn.dispatchEvent(ptr('pointerdown', 200, 100));
    btn.dispatchEvent(ptr('pointerup', 120, 100));
    btn.click();
    expect(cmp.got).toEqual([1]);
    expect(cmp.clicks).toBe(0);
    btn.click();
    expect(cmp.clicks).toBe(1);
  });

  it('still swallows a click that arrives 300 ms after the swipe, and lets one through after 400 ms', () => {
    vi.useFakeTimers();
    const { btn, cmp } = setup();
    btn.dispatchEvent(ptr('pointerdown', 200, 100));
    btn.dispatchEvent(ptr('pointerup', 120, 100));
    vi.advanceTimersByTime(300);
    btn.click();
    expect(cmp.clicks).toBe(0);

    btn.dispatchEvent(ptr('pointerdown', 200, 100));
    btn.dispatchEvent(ptr('pointerup', 120, 100));
    vi.advanceTimersByTime(401);
    btn.click();
    expect(cmp.clicks).toBe(1);
  });

  it('still emits when pointerup lands outside the host after capture (Review Focus 7)', () => {
    const fixture = TestBed.createComponent(HostComponent);
    fixture.detectChanges();
    const host = (fixture.nativeElement as HTMLElement).querySelector('[appSwipeX]') as HTMLElement;
    const capture = vi.fn();
    const release = vi.fn();
    host.setPointerCapture = capture;
    host.releasePointerCapture = release;
    host.dispatchEvent(ptr('pointerdown', 200, 100));
    expect(capture).toHaveBeenCalledWith(1);
    // jsdom does not retarget captured pointers, so the up goes to the host with out-of-box coordinates.
    host.dispatchEvent(ptr('pointerup', 120, 140));
    expect(fixture.componentInstance.got).toEqual([1]);
    expect(release).toHaveBeenCalledWith(1);
  });

  it('sets touch-action: pan-y on the host', () => {
    const { host } = setup();
    // jsdom's CSSStyleDeclaration does not model touch-action, so read the style attribute.
    expect(host.getAttribute('style')).toContain('touch-action: pan-y');
  });

  it('removes the pending click guard on destroy', () => {
    const { fixture, host, btn } = setup();
    const remove = vi.spyOn(host, 'removeEventListener');
    btn.dispatchEvent(ptr('pointerdown', 200, 100));
    btn.dispatchEvent(ptr('pointerup', 120, 100));
    fixture.destroy();
    expect(remove).toHaveBeenCalledWith('click', expect.any(Function), { capture: true });
  });
});
