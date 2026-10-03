import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Shown on MOZE-imported entries while `ACCOUNTING_IMPORT_LOCKED` is false (server answers 409 `locked_until_cutover`). */
@Component({
  selector: 'app-lock-banner',
  standalone: true,
  template: `
    @if (locked()) {
      <p class="lock-banner" role="status"><span aria-hidden="true">🔒</span> MOZE 匯入資料，切換後可編輯</p>
    }
  `,
  styles: `
    .lock-banner {
      align-items: center;
      background: var(--app-state-warning-bg);
      border-radius: var(--radius-md);
      color: var(--app-text);
      display: flex;
      font-size: var(--fs-sm);
      font-weight: var(--fw-semibold);
      gap: 6px;
      margin: 4px 0 8px;
      padding: 8px 12px;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LockBannerComponent {
  readonly locked = input(false);
}
