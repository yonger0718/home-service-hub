import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ACCOUNT_LOGOS } from '../../../theme/account-logos';
import { AccountLogoStore } from '../../../services/account-logo-store';
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

@Component({
  imports: [AccountIconComponent],
  template: `<app-account-icon [accountId]="id()" [logoHash]="hash()" [logo]="logo()" [icon]="icon()" />`,
})
class UploadHostComponent {
  id = signal<number | null>(7);
  hash = signal<string | null>('abc');
  logo = signal<string | null>(null);
  icon = signal<string | null>(null);
}

function uploadSetup(url: string | null) {
  TestBed.configureTestingModule({ providers: [{ provide: AccountLogoStore, useValue: { url: () => signal(url) } }] });
  const fixture = TestBed.createComponent(UploadHostComponent);
  fixture.detectChanges();
  const host = fixture.nativeElement.querySelector('app-account-icon') as HTMLElement;
  return { fixture, host, cmp: fixture.componentInstance };
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

  it('prefers the uploaded logo over the bundled one', () => {
    const { host, cmp } = uploadSetup('blob:1');
    expect(cmp).toBeTruthy();
    expect(host.querySelector('img')!.getAttribute('src')).toBe('blob:1');
    expect(host.classList.contains('has-logo')).toBe(true);
  });

  it('falls back to the bundled logo when the store has nothing', () => {
    const { fixture, host, cmp } = uploadSetup(null);
    cmp.logo.set('cathay-united');
    fixture.detectChanges();
    expect(host.querySelector('img')!.getAttribute('src')).toContain('assets/logos/');
  });

  it('falls back to the emoji when both are null', () => {
    const { host } = uploadSetup(null);
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('.emoji')).not.toBeNull();
    expect(host.classList.contains('has-logo')).toBe(false);
  });

  it('without an account id the bundled slug still renders (settings grid)', () => {
    const fixture = TestBed.createComponent(BigHostComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('img').getAttribute('src')).toBe(`assets/logos/${ACCOUNT_LOGOS[0].file}`);
  });
});

describe('AccountIconComponent with the real AccountLogoStore', () => {
  // Stubbing `URL` in place would leak the stubs into later specs of the same worker; restore the two methods.
  const originalObjectUrls = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
  afterEach(() => {
    vi.useRealTimers();
    Object.assign(URL, originalObjectUrls);
  });

  it('component + store: hash → null revokes the url and discards a pending response', () => {
    vi.useFakeTimers();
    let counter = 0;
    const revoke = vi.fn();
    const create = vi.fn(() => `blob:${++counter}`);
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const http = TestBed.inject(HttpTestingController);
    const A = 'a'.repeat(16);
    const B = 'b'.repeat(16);
    const etag = (h: string) => `"${h}${'0'.repeat(48)}"`;
    const fixture = TestBed.createComponent(UploadHostComponent);
    const host = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.hash.set(A);
    fixture.detectChanges();
    http.expectOne(r => r.url.includes(`v=${A}`)).flush(new Blob(['a']), { headers: { ETag: etag(A) } });
    fixture.detectChanges();
    expect(host.querySelector('img')!.getAttribute('src')).toBe('blob:1');

    fixture.componentInstance.hash.set(B);
    fixture.detectChanges();
    const pending = http.expectOne(r => r.url.includes(`v=${B}`));
    fixture.componentInstance.hash.set(null);
    fixture.detectChanges();
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('.emoji')).not.toBeNull();
    vi.runOnlyPendingTimers(); // deferred clear of the account slot
    vi.runOnlyPendingTimers(); // revoke on the following macrotask
    expect(revoke).toHaveBeenCalledWith('blob:1');

    pending.flush(new Blob(['b']), { headers: { ETag: etag(B) } });
    fixture.detectChanges();
    expect(create).toHaveBeenCalledTimes(1);
    expect(host.querySelector('img')).toBeNull();
    http.verify();
  });
});
