import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { ACCOUNT_LOGOS } from '../../../theme/account-logos';
import { AccountIconComponent } from './account-icon';

@Component({
  imports: [AccountIconComponent],
  template: `<app-account-icon class="ico" [logo]="logo()" [icon]="icon()" fallback="folder" />`,
})
class HostComponent {
  logo = signal<string | null>(null);
  icon = signal<string | null>(null);
}

@Component({
  imports: [AccountIconComponent],
  template: `<app-account-icon size="big" [logo]="slug" />`,
})
class BigHostComponent {
  slug = ACCOUNT_LOGOS[0].slug;
}

function setup() {
  const fixture = TestBed.createComponent(HostComponent);
  fixture.detectChanges();
  const host = fixture.nativeElement.querySelector('app-account-icon') as HTMLElement;
  return { fixture, host, cmp: fixture.componentInstance };
}

describe('AccountIconComponent', () => {
  it('renders the manifest image for a known slug', () => {
    const { fixture, host, cmp } = setup();
    cmp.logo.set(ACCOUNT_LOGOS[0].slug);
    fixture.detectChanges();
    const img = host.querySelector('img')!;
    expect(img.getAttribute('src')).toBe(`assets/logos/${ACCOUNT_LOGOS[0].file}`);
    expect(img.getAttribute('alt')).toBe('');
    expect(img.getAttribute('aria-hidden')).toBe('true');
    expect(host.querySelector('.emoji')).toBeNull();
  });

  it('renders the emoji for an unknown slug', () => {
    const { fixture, host, cmp } = setup();
    cmp.logo.set('nope');
    cmp.icon.set('💳');
    fixture.detectChanges();
    expect(host.querySelector('.emoji')!.textContent).toBe('💳');
    expect(host.querySelector('img')).toBeNull();
  });

  it('renders the fallback when neither logo nor icon', () => {
    const { host } = setup();
    expect(host.querySelector('.emoji')!.textContent).toBe('📁');
  });

  it('falls back to the emoji after the image errors and recovers on a new slug', () => {
    const { fixture, host, cmp } = setup();
    cmp.logo.set(ACCOUNT_LOGOS[0].slug);
    cmp.icon.set('💳');
    fixture.detectChanges();
    host.querySelector('img')!.dispatchEvent(new Event('error'));
    fixture.detectChanges();
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('.emoji')!.textContent).toBe('💳');
    cmp.logo.set(ACCOUNT_LOGOS[1].slug);
    fixture.detectChanges();
    expect(host.querySelector('img')).not.toBeNull();
  });

  it('host carries has-logo only while an image renders', () => {
    const { fixture, host, cmp } = setup();
    expect(host.classList.contains('has-logo')).toBe(false);
    cmp.logo.set(ACCOUNT_LOGOS[0].slug);
    fixture.detectChanges();
    expect(host.classList.contains('has-logo')).toBe(true);
    host.querySelector('img')!.dispatchEvent(new Event('error'));
    fixture.detectChanges();
    expect(host.classList.contains('has-logo')).toBe(false);
  });

  it('paints the tile on an inner element, not the host', () => {
    const { fixture, host, cmp } = setup();
    cmp.logo.set(ACCOUNT_LOGOS[0].slug);
    fixture.detectChanges();
    expect(host.querySelector('img')!.parentElement!.classList.contains('tile')).toBe(true);
  });

  it('size="big" sets the host class big; a logo sets has-logo', () => {
    const fixture = TestBed.createComponent(BigHostComponent);
    fixture.detectChanges();
    const host = fixture.nativeElement.querySelector('app-account-icon') as HTMLElement;
    expect(host.classList.contains('big')).toBe(true);
    expect(host.classList.contains('has-logo')).toBe(true);
  });
});
