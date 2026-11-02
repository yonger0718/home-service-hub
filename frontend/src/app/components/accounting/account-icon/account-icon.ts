import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';

import { AccountLogoStore } from '../../../services/account-logo-store';
import { accountLogo } from '../../../theme/account-logos';

type Fallback = 'folder' | 'bank' | 'card';

/** Emoji shown when an account has neither a usable logo nor an icon. Kept out of the template (line-icons.spec). */
const FALLBACK_EMOJI: Record<Fallback, string> = { folder: '📁', bank: '🏦', card: '💳' };

/** An account's icon slot: the uploaded logo (one store lease per icon), else the bundled institution logo on a white tile, else the emoji. Sized by the slot's `--ico-size`. */
@Component({
  selector: 'app-account-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[class.has-logo]': 'uploadedUrl() !== null || entry() !== undefined', '[class.big]': "size() === 'big'" },
  template: `
    @if (uploadedUrl(); as u) {
      <span class="tile"><img class="logo" [src]="u" alt="" aria-hidden="true" decoding="async" /></span>
    } @else if (entry(); as e) {
      <span class="tile"><img class="logo" [src]="'assets/logos/' + e.file" alt="" aria-hidden="true" loading="lazy" decoding="async" (error)="failedSlug.set(e.slug)" /></span>
    } @else {
      <span class="emoji">{{ icon() || FALLBACK_EMOJI[fallback()] }}</span>
    }
  `,
  styles: `
    :host { display: inline-flex; align-items: center; justify-content: center; overflow: hidden; width: var(--ico-size, 1.25em); height: var(--ico-size, 1.25em); flex: none; }
    .tile { display: block; width: 100%; height: 100%; background: var(--app-logo-tile); border-radius: inherit; }
    .logo { width: 100%; height: 100%; object-fit: contain; padding: 0; display: block; }
    .emoji { line-height: 1; }
  `,
})
export class AccountIconComponent {
  readonly logo = input<string | null>(null);
  readonly accountId = input<number | null>(null);
  readonly logoHash = input<string | null>(null);
  readonly icon = input<string | null>(null);
  readonly fallback = input<Fallback>('folder');
  readonly size = input<'row' | 'big'>('row');

  private readonly store = inject(AccountLogoStore);

  protected readonly FALLBACK_EMOJI = FALLBACK_EMOJI;
  protected readonly failedSlug = signal<string | null>(null);
  /** The account's image from the store's per-account slot; reading it never fetches (the lease below does). */
  protected readonly uploadedUrl = computed(() => {
    const id = this.accountId();
    return id !== null ? this.store.slot(id)() : null;
  });
  protected readonly entry = computed(() => {
    const s = this.logo();
    return s && this.failedSlug() !== s ? accountLogo(s) : undefined;
  });

  constructor() {
    // Every <app-account-icon> holds one lease for its current (accountId, logoHash); the store fetches for the
    // newest non-null lease of an account only, and a null hash is forwarded so a deletion can clear the slot once
    // no lease of the account wants an image. Re-evaluation of the computed above never fetches.
    effect(onCleanup => {
      const id = this.accountId();
      const hash = this.logoHash() ?? null;
      if (id === null) {
        return;
      }
      const lease = untracked(() => this.store.acquire(id, hash));
      onCleanup(() => lease.release());
    });
  }
}
