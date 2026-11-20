import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { AccountingOverviewService } from '../month-overview/overview.service';

/** The title-row 搜尋與篩選 button of 日曆 below 1024 px; the timeline reads `calendarFiltersOpen` to show its filter bar row. */
@Component({
  selector: 'app-cal-filters-toggle',
  standalone: true,
  template: `<button
    type="button"
    class="cal-filters-toggle"
    [class.has-active]="overview.calendarFiltersActive()"
    aria-label="搜尋與篩選"
    [attr.aria-expanded]="overview.calendarFiltersOpen()"
    (click)="overview.calendarFiltersOpen.set(!overview.calendarFiltersOpen())"
  >
    <i class="pi pi-search" aria-hidden="true"></i>
  </button>`,
  styles: `
    :host {
      display: inline-flex;
    }
    .cal-filters-toggle {
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 40px;
      height: 40px;
      padding: 0;
      border-radius: 50%;
      border: 1px solid var(--app-border);
      background: var(--app-surface);
      color: var(--app-text);
      cursor: pointer;
    }
    .cal-filters-toggle[aria-expanded='true'] {
      border-color: var(--app-primary);
    }
    .cal-filters-toggle.has-active::after {
      content: '';
      position: absolute;
      top: 6px;
      right: 6px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--app-primary);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CalFiltersToggleComponent {
  protected readonly overview = inject(AccountingOverviewService);
}
