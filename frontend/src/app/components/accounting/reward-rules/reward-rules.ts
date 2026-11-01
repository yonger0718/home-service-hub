import { Location } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, ParamMap, Router, RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { RewardRuleOverview, RewardRuleOverviewQuery, RewardRuleState, RewardRuleStats } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { leavePage } from '../accounting-ui';
import { slashDate } from '../dates';
import { formatMoney } from '../format';
import { writeErrorMessage } from '../http-errors';
import { RULE_STATE_LABELS, periodText, postingSummary, ruleLabel } from '../reward-rule-text';

export type RewardRuleFilter = RewardRuleState | 'all';

const FILTERS: readonly { key: RewardRuleFilter; label: string }[] = [
  { key: 'active', label: '啟用中' },
  { key: 'expired', label: '已到期' },
  { key: 'upcoming', label: '尚未開始' },
  { key: 'disabled', label: '已停用' },
  { key: 'all', label: '全部' },
];

/** `綁定 N 筆（近 12 月 M）· 已入帳 K 筆 · 合計 X, Y · 最近 yyyy/MM/dd`; 合計 / 最近 only when there is something to show. */
export function ruleStatsText(stats: RewardRuleStats): string {
  let text = `綁定 ${stats.linked_entries} 筆（近 12 月 ${stats.linked_entries_12m}）· 已入帳 ${stats.rewards_count} 筆`;
  if (stats.rewards_total.length) {
    text += ` · 合計 ${stats.rewards_total.map(total => formatMoney(total.amount, total.currency)).join(', ')}`;
  }
  if (stats.rewards_last_date) {
    text += ` · 最近 ${slashDate(stats.rewards_last_date)}`;
  }
  return text;
}

function filterFrom(params: ParamMap): RewardRuleFilter {
  const state = params.get('state');
  return FILTERS.some(option => option.key === state) ? (state as RewardRuleFilter) : 'active';
}

/** 回饋規則總覽: every card's reward rules with their usage, filtered by state through the URL. */
@Component({
  selector: 'app-reward-rules',
  imports: [RouterLink],
  templateUrl: './reward-rules.html',
  styleUrl: './reward-rules.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RewardRulesOverviewComponent {
  private readonly accountingService = inject(AccountingService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly layoutMode = inject(LayoutModeService);

  protected readonly filters = FILTERS;
  protected readonly stateLabels = RULE_STATE_LABELS;
  protected readonly ruleLabel = ruleLabel;
  protected readonly postingSummary = postingSummary;
  protected readonly periodText = periodText;
  protected readonly statsText = ruleStatsText;

  private readonly params = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  readonly filter = computed(() => filterFrom(this.params()));
  readonly showArchived = computed(() => this.params().get('archived') === '1');
  private readonly accountIds = computed(() =>
    this.params()
      .getAll('account_id')
      .map(Number)
      .filter(id => Number.isInteger(id) && id > 0),
  );

  readonly overview = signal<RewardRuleOverview | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly callout = computed(() => (this.overview()?.counts.enabled_expired ?? 0) > 0);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');

  private loadSub: Subscription | null = null;

  constructor() {
    // A rule edited on an account's settings pane bumps accountsChanged: the overview re-reads its stats.
    effect(() => {
      const query: RewardRuleOverviewQuery = {
        account_id: this.accountIds().length ? this.accountIds() : undefined,
        state: this.filter(),
        include_archived_accounts: this.showArchived() || undefined,
      };
      this.accountingService.accountsChanged();
      untracked(() => this.load(query));
    });
    inject(DestroyRef).onDestroy(() => this.loadSub?.unsubscribe());
  }

  setFilter(state: RewardRuleFilter): void {
    this.navigate({ state });
  }

  setArchived(checked: boolean): void {
    this.navigate({ archived: checked ? '1' : null });
  }

  back(): void {
    leavePage(this.router, this.location, '/accounting/settings');
  }

  private load(query: RewardRuleOverviewQuery): void {
    this.loadSub?.unsubscribe();
    this.loading.set(true);
    this.error.set(null);
    this.loadSub = this.accountingService.getRewardRuleOverview(query).subscribe({
      next: overview => {
        this.overview.set(overview);
        this.loading.set(false);
      },
      error: err => {
        this.error.set(writeErrorMessage(err));
        this.loading.set(false);
      },
    });
  }

  private navigate(queryParams: Record<string, string | null>): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams, queryParamsHandling: 'merge', replaceUrl: true });
  }
}
