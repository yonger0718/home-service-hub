import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { FilterChip } from './filter-bar';

/** The removable chip row of an active filter set; used by the filter bar and by the 日曆 right pane. */
@Component({
  selector: 'app-filter-chips',
  standalone: true,
  templateUrl: './filter-chips.html',
  styleUrl: './filter-chips.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FilterChipsComponent {
  readonly chips = input<FilterChip[]>([]);
  readonly remove = output<string>();
  readonly clearAll = output<void>();
}
