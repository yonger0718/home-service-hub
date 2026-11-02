import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  const stub = { slot: () => signal(url), acquire: () => ({ release: () => undefined }) };
  TestBed.configureTestingModule({ providers: [{ provide: AccountLogoStore, useValue: stub }] });
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

@Component({
  imports: [AccountIconComponent],
  template: `
    <app-account-icon class="detail" [accountId]="7" [logoHash]="detail()" />
    <app-account-icon class="list" [accountId]="7" [logoHash]="list()" />
  `,
})
class TwoSlotsHostComponent {
  detail = signal<string | null>(null);
  list = signal<string | null>(null);
}

@Component({
  imports: [AccountIconComponent],
  template: `
    <app-account-icon class="detail" [accountId]="7" [logoHash]="detail()" />
    @if (showList()) {
      <app-account-icon class="list" [accountId]="7" [logoHash]="list()" />
    }
  `,
})
class ToggledHostComponent {
  detail = signal<string | null>(null);
  list = signal<string | null>(null);
  showList = signal(true);
}

describe('AccountIconComponent with the real AccountLogoStore', () => {
  // Stubbing `URL` in place would leak the stubs into later specs of the same worker; restore the two methods.
  const originalObjectUrls = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
  const A = 'a'.repeat(16);
  const B = 'b'.repeat(16);
  const etag = (h: string) => `"${h}${'0'.repeat(48)}"`;
  const P = (r: { url: string }) => r.url.startsWith('/api/accounting/accounts/7/logo.png');
  let http: HttpTestingController;
  let revoke: ReturnType<typeof vi.fn>;
  let create: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    let counter = 0;
    revoke = vi.fn();
    create = vi.fn(() => `blob:${++counter}`);
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
    Object.assign(URL, originalObjectUrls);
  });

  const srcOf = (root: HTMLElement, selector: string) => root.querySelector(`${selector} img`)?.getAttribute('src') ?? null;

  it('one icon: hash → null keeps the image until the deferred clear, then revokes it and discards a pending response', () => {
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
    expect(host.querySelector('img')!.getAttribute('src')).toBe('blob:1'); // until the clear fires
    vi.runOnlyPendingTimers(); // deferred clear of the account slot
    fixture.detectChanges();
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('.emoji')).not.toBeNull();
    vi.runOnlyPendingTimers(); // revoke on the following macrotask
    expect(revoke).toHaveBeenCalledWith('blob:1');

    pending.flush(new Blob(['b']), { headers: { ETag: etag(B) } });
    fixture.detectChanges();
    expect(create).toHaveBeenCalledTimes(1);
    expect(host.querySelector('img')).toBeNull();
    http.expectNone(P);
  });

  it('two icons of one account: one GET, both show the image; the list moving A → B → A reloads once per move (W1)', () => {
    const fixture = TestBed.createComponent(TwoSlotsHostComponent);
    const root = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.detail.set(A);
    fixture.componentInstance.list.set(A);
    fixture.detectChanges();
    http.expectOne(r => r.url.includes(`v=${A}`)).flush(new Blob(['a']), { headers: { ETag: etag(A) } });
    fixture.detectChanges();
    expect(srcOf(root, '.detail')).toBe('blob:1');
    expect(srcOf(root, '.list')).toBe('blob:1');
    vi.advanceTimersByTime(1);
    http.expectNone(P);

    fixture.componentInstance.list.set(B); // the list reloaded; the detail still holds A
    fixture.detectChanges();
    http.expectOne(r => r.url.includes(`v=${B}`)).flush(new Blob(['b']), { headers: { ETag: etag(B) } });
    fixture.detectChanges();
    expect(srcOf(root, '.detail')).toBe('blob:2');
    expect(srcOf(root, '.list')).toBe('blob:2');
    vi.advanceTimersByTime(1);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    http.expectNone(P);

    fixture.componentInstance.list.set(A); // the earlier logo uploaded again
    fixture.detectChanges();
    http.expectOne(r => r.url.includes(`v=${A}`)).flush(new Blob(['a']), { headers: { ETag: etag(A) } });
    fixture.detectChanges();
    expect(srcOf(root, '.detail')).toBe('blob:3');
    expect(srcOf(root, '.list')).toBe('blob:3');
    vi.advanceTimersByTime(31_001);
    fixture.detectChanges();
    http.expectNone(P);
    expect(create).toHaveBeenCalledTimes(3);
  });

  it('two icons: a stale null detail beside the list\'s A clears nothing; both null clears the slot once', () => {
    const fixture = TestBed.createComponent(TwoSlotsHostComponent);
    const root = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.list.set(A); // detail stays null (one-shot payload from before the upload)
    fixture.detectChanges();
    http.expectOne(r => r.url.includes(`v=${A}`)).flush(new Blob(['a']), { headers: { ETag: etag(A) } });
    fixture.detectChanges();
    expect(srcOf(root, '.detail')).toBe('blob:1');
    expect(srcOf(root, '.list')).toBe('blob:1');
    vi.advanceTimersByTime(31_001);
    fixture.detectChanges();
    http.expectNone(P);
    expect(revoke).not.toHaveBeenCalled();

    fixture.componentInstance.list.set(null); // deletion
    fixture.detectChanges();
    vi.runOnlyPendingTimers();
    fixture.detectChanges();
    expect(root.querySelector('img')).toBeNull();
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
    http.expectNone(P);
  });

  it('an @if-toggled icon destroyed and recreated with the same hash beside an older-hash icon keeps the newest image', () => {
    const fixture = TestBed.createComponent(ToggledHostComponent);
    const root = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.detail.set(A); // stale one-shot payload
    fixture.componentInstance.list.set(B);
    fixture.detectChanges();
    const reqA = http.expectOne(r => r.url.includes(`v=${A}`));
    http.expectOne(r => r.url.includes(`v=${B}`)).flush(new Blob(['b']), { headers: { ETag: etag(B) } });
    reqA.flush(new Blob(['a']), { headers: { ETag: etag(A) } }); // superseded
    fixture.detectChanges();
    expect(srcOf(root, '.detail')).toBe('blob:1');
    expect(srcOf(root, '.list')).toBe('blob:1');

    fixture.componentInstance.showList.set(false); // the list icon is destroyed: A becomes the target
    fixture.detectChanges();
    const reqA2 = http.expectOne(r => r.url.includes(`v=${A}`));
    fixture.componentInstance.showList.set(true); // and recreated with B: the A load is cancelled
    fixture.detectChanges();
    expect(reqA2.cancelled).toBe(true);
    expect(srcOf(root, '.detail')).toBe('blob:1');
    expect(srcOf(root, '.list')).toBe('blob:1');
    vi.advanceTimersByTime(31_001);
    fixture.detectChanges();
    http.expectNone(P);
    expect(create).toHaveBeenCalledTimes(1);
    expect(revoke).not.toHaveBeenCalled();
  });

  it('destroying the icons releases their leases: the slot clears and the URL is revoked', () => {
    const fixture = TestBed.createComponent(TwoSlotsHostComponent);
    fixture.componentInstance.detail.set(A);
    fixture.componentInstance.list.set(A);
    fixture.detectChanges();
    http.expectOne(r => r.url.includes(`v=${A}`)).flush(new Blob(['a']), { headers: { ETag: etag(A) } });
    fixture.destroy();
    vi.advanceTimersByTime(2);
    expect(TestBed.inject(AccountLogoStore).slot(7)()).toBeNull();
    expect(revoke).toHaveBeenCalledWith('blob:1');
    http.expectNone(P);
  });
});
