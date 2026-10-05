import { ChangeDetectionStrategy, Component, DestroyRef, Injectable, inject, signal } from '@angular/core';

import { writeErrorMessage } from './http-errors';
import { IMPORT_RUNNING_TOAST, isImportRunning } from './schedule-math';

/** One short message at the bottom of the accounting pages (`匯入進行中，請稍後再試`, D43). */
@Injectable({ providedIn: 'root' })
export class AccountingToastService {
  private readonly text = signal<string | null>(null);
  private timer: ReturnType<typeof setTimeout> | null = null;

  readonly message = this.text.asReadonly();

  show(text: string, ms = 3000): void {
    this.clear();
    this.text.set(text);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.text.set(null);
    }, ms);
  }

  clear(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.text.set(null);
  }
}

/** A failed schedule action: `import_running` shows the toast (page state unchanged) → null; else the error line. */
export function scheduleActionError(err: unknown, toast: AccountingToastService): string | null {
  if (isImportRunning(err)) {
    toast.show(IMPORT_RUNNING_TOAST);
    return null;
  }
  return writeErrorMessage(err);
}

@Component({
  selector: 'app-accounting-toast',
  standalone: true,
  template: `
    <div role="status" aria-live="polite">
      @if (toast.message(); as text) {
        <p class="accounting-toast">{{ text }}</p>
      }
    </div>
  `,
  styles: [
    `
      .accounting-toast {
        background: var(--app-text);
        border-radius: var(--radius-pill);
        bottom: calc(80px + env(safe-area-inset-bottom, 0px));
        color: var(--app-surface);
        font-size: var(--fs-sm, 0.85rem);
        font-weight: 700;
        left: 50%;
        margin: 0;
        max-width: calc(100vw - 32px);
        padding: 8px 16px;
        position: fixed;
        transform: translateX(-50%);
        z-index: 1200;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingToastComponent {
  readonly toast = inject(AccountingToastService);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.toast.clear());
  }
}
