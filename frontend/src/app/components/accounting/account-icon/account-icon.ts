import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { accountLogo } from '../../../theme/account-logos';

type Fallback = 'folder' | 'bank' | 'card';

/** Emoji shown when an account has neither a usable logo nor an icon. Kept out of the template (line-icons.spec). */
const FALLBACK_EMOJI: Record<Fallback, string> = { folder: '📁', bank: '🏦', card: '💳' };

/** An account's icon slot: the bundled institution logo on a white tile, else the emoji. Sized by the slot's `--ico-size`. */
@Component({
  selector: 'app-account-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[class.has-logo]': 'entry() !== undefined', '[class.big]': "size() === 'big'" },
  template: `
    @if (entry(); as e) {
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
  readonly icon = input<string | null>(null);
  readonly fallback = input<Fallback>('folder');
  readonly size = input<'row' | 'big'>('row');

  protected readonly FALLBACK_EMOJI = FALLBACK_EMOJI;
  protected readonly failedSlug = signal<string | null>(null);
  protected readonly entry = computed(() => {
    const s = this.logo();
    return s && this.failedSlug() !== s ? accountLogo(s) : undefined;
  });
}
