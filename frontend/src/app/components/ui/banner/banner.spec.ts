import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BANNER_DISMISSALS_KEY } from './banner-dismissals';
import { BannerComponent, BannerTone } from './banner';

@Component({
  imports: [BannerComponent],
  template: `<app-banner
    [tone]="tone()"
    heading="測試標題"
    [detail]="detail()"
    [actionLabel]="actionLabel()"
    [actionDisabled]="disabled()"
    [dismissId]="dismissId()"
    (action)="clicks = clicks + 1"
  />`,
})
class HostComponent {
  readonly tone = signal<BannerTone>('error');
  readonly detail = signal<string | null>('測試說明');
  readonly actionLabel = signal<string | null>('重試');
  readonly disabled = signal(false);
  readonly dismissId = signal<string | null>(null);
  clicks = 0;
}

function make() {
  const fixture = TestBed.createComponent(HostComponent);
  fixture.detectChanges();
  const q = (sel: string) => fixture.nativeElement.querySelector(sel) as HTMLElement | null;
  return { fixture, host: fixture.componentInstance, q };
}

describe('BannerComponent', () => {
  beforeEach(() => localStorage.removeItem(BANNER_DISMISSALS_KEY));
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(BANNER_DISMISSALS_KEY);
  });

  it('error renders role alert with heading, detail and action, and no close even with a dismissId', () => {
    const { host, fixture, q } = make();
    host.dismissId.set('test-error');
    fixture.detectChanges();
    expect(q('.banner')?.getAttribute('role')).toBe('alert');
    expect(q('.banner')?.classList).toContain('banner--error');
    expect(q('.banner-icon')?.classList).toContain('pi-exclamation-circle');
    expect(q('.banner-icon')?.getAttribute('aria-hidden')).toBe('true');
    expect(q('.banner-title')?.textContent).toBe('測試標題');
    expect(q('.banner-detail')?.textContent).toBe('測試說明');
    expect(q('.banner-action')?.textContent?.trim()).toBe('重試');
    expect(q('.banner-close')).toBeNull();
  });

  it('warn and info render role status', () => {
    const { host, fixture, q } = make();
    host.tone.set('warn');
    fixture.detectChanges();
    expect(q('.banner')?.getAttribute('role')).toBe('status');
    expect(q('.banner-icon')?.classList).toContain('pi-exclamation-triangle');
    host.tone.set('info');
    fixture.detectChanges();
    expect(q('.banner')?.getAttribute('role')).toBe('status');
    expect(q('.banner-icon')?.classList).toContain('pi-info-circle');
  });

  it('the action emits once per click and honours actionDisabled', () => {
    const { host, fixture, q } = make();
    (q('.banner-action') as HTMLButtonElement).click();
    expect(host.clicks).toBe(1);
    host.disabled.set(true);
    fixture.detectChanges();
    expect((q('.banner-action') as HTMLButtonElement).disabled).toBe(true);
    (q('.banner-action') as HTMLButtonElement).click();
    expect(host.clicks).toBe(1);
  });

  it('no detail → no .banner-detail; no actionLabel → no .banner-action', () => {
    const { host, fixture, q } = make();
    host.detail.set(null);
    host.actionLabel.set(null);
    fixture.detectChanges();
    expect(q('.banner-detail')).toBeNull();
    expect(q('.banner-action')).toBeNull();
  });

  it('info with a dismissId shows 關閉; closing hides it and stores the id', () => {
    const { host, fixture, q } = make();
    host.tone.set('info');
    host.dismissId.set('test-info');
    fixture.detectChanges();
    const close = q('.banner-close') as HTMLButtonElement;
    expect(close.getAttribute('aria-label')).toBe('關閉');
    close.click();
    fixture.detectChanges();
    expect(q('.banner')).toBeNull();
    expect(JSON.parse(localStorage.getItem(BANNER_DISMISSALS_KEY) ?? '[]')).toContain('test-info');
  });

  it('a dismissed info id stays hidden in a new instance; another id still shows', () => {
    localStorage.setItem(BANNER_DISMISSALS_KEY, JSON.stringify(['test-info']));
    const first = make();
    first.host.tone.set('info');
    first.host.dismissId.set('test-info');
    first.fixture.detectChanges();
    expect(first.q('.banner')).toBeNull();
    const second = make();
    second.host.tone.set('info');
    second.host.dismissId.set('other-info');
    second.fixture.detectChanges();
    expect(second.q('.banner')).not.toBeNull();
  });

  it('info without a dismissId has no close', () => {
    const { host, fixture, q } = make();
    host.tone.set('info');
    fixture.detectChanges();
    expect(q('.banner')).not.toBeNull();
    expect(q('.banner-close')).toBeNull();
  });

  it('storage that throws: renders, closes, stays closed for the session', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const { host, fixture, q } = make();
    host.tone.set('info');
    host.dismissId.set('test-info');
    fixture.detectChanges();
    expect(q('.banner')).not.toBeNull();
    (q('.banner-close') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(q('.banner')).toBeNull();
    const again = make();
    again.host.tone.set('info');
    again.host.dismissId.set('test-info');
    again.fixture.detectChanges();
    expect(again.q('.banner')).toBeNull();
  });

  it('warn ignores dismissId: no close, not hidden when the id is stored', () => {
    localStorage.setItem(BANNER_DISMISSALS_KEY, JSON.stringify(['test-warn']));
    const { host, fixture, q } = make();
    host.tone.set('warn');
    host.dismissId.set('test-warn');
    fixture.detectChanges();
    expect(q('.banner')).not.toBeNull();
    expect(q('.banner-close')).toBeNull();
    expect(q('.banner')?.classList).not.toContain('banner--closable');
  });

  it('layout: close is pinned top-right and its width reserved; only the action wraps; body breaks long words', () => {
    const { host, fixture, q } = make();
    host.tone.set('info');
    host.dismissId.set('test-info');
    fixture.detectChanges();
    expect(q('.banner')?.classList).toContain('banner--closable');
    // DOM order: text, action, close
    const kids = Array.from(q('.banner')!.children).map((c) => c.className.split(' ')[0]);
    expect(kids).toEqual(['banner-icon', 'banner-body', 'banner-action', 'banner-close']);
    const scss = readFileSync(join(process.cwd(), 'src/app/components/ui/banner/banner.scss'), 'utf8');
    expect(scss).toMatch(/\.banner-close\s*\{[^}]*position:\s*absolute;[^}]*top:[^}]*right:/);
    expect(scss).toMatch(/\.banner--closable\s*\{\s*padding-right:\s*48px/);
    expect(scss).toMatch(/\.banner-body\s*\{[^}]*flex:\s*1 1 180px;[^}]*overflow-wrap:\s*anywhere/);
  });

  it('scss uses tokens only', () => {
    const scss = readFileSync(join(process.cwd(), 'src/app/components/ui/banner/banner.scss'), 'utf8');
    for (const t of [
      'var(--c-red-soft)',
      'var(--c-red-pill-ink)',
      'var(--app-banner-warn-soft)',
      'var(--app-banner-warn)',
      'var(--app-primary-soft)',
    ]) {
      expect(scss).toContain(t);
    }
    expect(scss).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
  });
});
