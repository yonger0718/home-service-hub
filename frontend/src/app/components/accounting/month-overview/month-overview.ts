import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { MonthSummary } from '../../../models/accounting.model';
import { monthLabel } from '../dates';
import { barSegments, categoryDistribution, distributionAriaText } from '../timeline/category-distribution';
import { DueItem } from './due-items';
import type { OverviewState } from './overview.service';

/** The accounting layout's empty pane at ≥ 1280 px: the month's category distribution and the next dues (no links, D7). */
@Component({
  selector: 'app-month-overview',
  templateUrl: './month-overview.html',
  styleUrl: './month-overview.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MonthOverviewComponent {
  readonly month = input.required<string>();
  readonly summary = input<MonthSummary | null>(null);
  readonly dueItems = input<DueItem[]>([]);
  readonly summaryState = input<OverviewState>('ready');
  readonly dueState = input<OverviewState>('ready');

  protected readonly monthLabel = monthLabel;
  protected readonly distribution = computed(() => categoryDistribution(this.summary()));
  protected readonly bar = computed(() => barSegments(this.distribution()));
  protected readonly aria = computed(() => distributionAriaText(this.distribution()));
}
