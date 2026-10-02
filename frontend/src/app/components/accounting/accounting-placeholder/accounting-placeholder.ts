import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Stand-in for accounting pages whose component lands in a later task (see `ACCOUNTING_PAGES`). */
@Component({
  selector: 'app-accounting-placeholder',
  standalone: true,
  template: `<p class="accounting-placeholder">此頁面建置中</p>`,
  styles: `
    .accounting-placeholder {
      color: var(--app-text-muted);
      font-size: var(--fs-sm);
      padding: 2rem 1rem;
      text-align: center;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingPlaceholderComponent {}
