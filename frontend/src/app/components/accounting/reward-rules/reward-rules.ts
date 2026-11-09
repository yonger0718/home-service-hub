import { Location } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, ParamMap, Router, RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import {
  RewardRuleCounts, RewardRuleOverview, RewardRuleOverviewAccount, RewardRuleOverviewQuery, RewardRuleOverviewRule,
  RewardRuleState, RewardUsage,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { CapBarComponent } from '../../ui/cap-bar/cap-bar';
import { AccountIconComponent } from '../account-icon/account-icon';
import { leavePage } from '../accounting-ui';
import { formatMoney } from '../format';
import { writeErrorMessage } from '../http-errors';
import {
  RULE_STATE_LABELS, RewardUnit, fullDate, postingSummary, rateChip, rewardFigure, ruleTitle, usageUnit, windowText,
} from '../reward-rule-text';

export type RewardRuleFilter = RewardRuleState | 'all';

/** Chip order of the spec (D3); 全部 first because the rules-less row exists only there. */
const FILTERS: readonly { key: RewardRuleFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '生效中' },
  { key: 'disabled', label: '已停用' },
  { key: 'expired', label: '已過期' },
  { key: 'upcoming', label: '未開始' },
];

export interface OverviewRow {
  rule: RewardRuleOverviewRule;
  title: string;
  rate: string;
  posting: string;
  /** The state tag, or 基本; null on a plain active rule. */
  tag: { label: string; state: RewardRuleState | 'basic' } | null;
  /** The usage row behind the cap bar — active rules only. */
  usage: RewardUsage | null;
  unit: RewardUnit | null;
  /** The muted line in place of the bar on a non-active rule. */
  muted: string | null;
  /** 「本期 M/D–M/D」 when the card's rows have different windows. */
  window: string | null;
}

export interface OverviewCard {
  account: RewardRuleOverviewAccount;
  name: string;
  window: string;
  /** One period total per unit, money first. */
  totals: string[];
  rows: OverviewRow[];
  linked12m: number;
}

function filterFrom(params: ParamMap): RewardRuleFilter {
  const state = params.get('state');
  return FILTERS.some(option => option.key === state) ? (state as RewardRuleFilter) : 'all';
}

function unitKey(row: RewardUsage): string {
  return row.reward_is_money ? '$' : `#${row.reward_account_id}`;
}

/** The sum of `used + estimated` per unit; money first, then the points units in order of appearance; `$0` with no rows. */
export function cardTotals(usage: RewardUsage[]): string[] {
  const sums = new Map<string, { unit: RewardUnit; total: number }>();
  for (const row of usage) {
    const entry = sums.get(unitKey(row)) ?? { unit: usageUnit(row), total: 0 };
    entry.total += Number(row.used) + Number(row.estimated);
    sums.set(unitKey(row), entry);
  }
  if (sums.size === 0) {
    return [formatMoney(0, 'TWD')];
  }
  const entries = [...sums.values()];
  return [...entries.filter(e => e.unit.money), ...entries.filter(e => !e.unit.money)].map(e => rewardFigure(e.total, e.unit));
}

/** `2026-11-01`–`2026-11-30`: the first to the last day of one month. */
function isWholeMonth(start: string, end: string): boolean {
  if (start.slice(8, 10) !== '01' || start.slice(0, 7) !== end.slice(0, 7)) {
    return false;
  }
  const lastDay = new Date(Date.UTC(Number(end.slice(0, 4)), Number(end.slice(5, 7)), 0)).getUTCDate();
  return Number(end.slice(8, 10)) === lastDay;
}

/** A row is a calendar window when its first listed rule says so; with none of its rules listed (a state filter hid
 * them, while `usage` ignores the filter) a whole calendar month counts as one. */
function rowIsCalendar(row: RewardUsage, rules: RewardRuleOverviewRule[]): boolean {
  const first = row.rule_ids.map(id => rules.find(rule => rule.id === id)).find(rule => rule !== undefined);
  return first ? first.window === 'calendar_month' : isWholeMonth(row.window_start, row.window_end);
}

/** Different `(window_start, window_end)` pairs — a statement cycle and a calendar month can share a start. */
function isMixed(usage: RewardUsage[]): boolean {
  return new Set(usage.map(row => `${row.window_start}/${row.window_end}`)).size > 1;
}

function windowLine(account: RewardRuleOverviewAccount, mixed: boolean): string {
  const closing = account.closing_day === null ? '' : `結帳日 ${account.closing_day} 號`;
  const [row] = account.usage;
  if (!row || mixed) {
    return closing;
  }
  const period = windowText(row);
  if (account.usage.every(candidate => rowIsCalendar(candidate, account.rules))) {
    return `每月 1 日起算 · 本月 ${period}`;
  }
  return closing ? `${closing} · 本期 ${period}` : `本期 ${period}`;
}

function mutedLine(rule: RewardRuleOverviewRule): string | null {
  switch (rule.state) {
    case 'disabled':
      return '停用中，不估算回饋';
    case 'expired':
      return '已結束，不再估算';
    case 'upcoming':
      return rule.starts_on ? `${fullDate(rule.starts_on)} 起才估算` : '尚未開始';
    default:
      return null;
  }
}

export function buildCard(account: RewardRuleOverviewAccount): OverviewCard {
  const mixed = isMixed(account.usage);
  const rows = account.rules.map((rule): OverviewRow => {
    const usage = account.usage.find(row => row.rule_ids.includes(rule.id)) ?? null;
    const active = rule.state === 'active';
    return {
      rule,
      title: ruleTitle(rule),
      rate: rateChip(rule),
      posting: postingSummary(rule),
      tag: !active
        ? { label: RULE_STATE_LABELS[rule.state], state: rule.state }
        : rule.is_basic ? { label: '基本', state: 'basic' } : null,
      usage: active ? usage : null,
      unit: active && usage ? usageUnit(usage) : null,
      muted: mutedLine(rule),
      window: mixed && usage ? `本期 ${windowText(usage)}` : null,
    };
  });
  return {
    account,
    name: `${account.account_name}${account.is_archived ? '（已封存）' : ''}`,
    window: windowLine(account, mixed),
    totals: cardTotals(account.usage),
    rows,
    linked12m: account.rules.reduce((sum, rule) => sum + rule.stats.linked_entries_12m, 0),
  };
}

/** `N 張卡 · M 條規則 · 本期回饋合計 $X（含預估）`, then ` ＋ K 種點數` for the distinct non-money units (D1a). */
export function headlineText(cards: OverviewCard[], counts: RewardRuleCounts): string {
  const rules = counts.active + counts.disabled + counts.expired + counts.upcoming;
  let money = 0;
  const points = new Set<number>();
  for (const card of cards) {
    for (const row of card.account.usage) {
      if (row.reward_is_money) {
        money += Number(row.used) + Number(row.estimated);
      } else {
        points.add(row.reward_account_id);
      }
    }
  }
  const head = `${cards.length} 張卡 · ${rules} 條規則 · 本期回饋合計 ${formatMoney(money, 'TWD')}（含預估）`;
  return points.size ? `${head} ＋ ${points.size} 種點數` : head;
}

/** 回饋規則總覽 (D3): one card per account with rules, their period usage as cap bars, filtered by state through the URL. */
@Component({
  selector: 'app-reward-rules',
  imports: [RouterLink, AccountIconComponent, CapBarComponent],
  templateUrl: './reward-rules.html',
  styleUrl: './reward-rules.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RewardRulesOverviewComponent {
  private readonly accountingService = inject(AccountingService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly location = inject(Location);

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

  readonly cards = computed(() => (this.overview()?.accounts ?? []).filter(a => a.rules.length > 0).map(buildCard));
  /** Credit accounts without a listed rule — only under 全部, where "no rules" is what an empty list means. */
  readonly bare = computed(() =>
    this.filter() === 'all' ? (this.overview()?.accounts ?? []).filter(a => a.is_credit && a.rules.length === 0) : [],
  );
  readonly headline = computed(() => {
    const data = this.overview();
    return data ? headlineText(this.cards(), data.counts) : '';
  });
  readonly chipCounts = computed<Record<RewardRuleFilter, number>>(() => {
    const counts = this.overview()?.counts;
    const of = (state: RewardRuleState): number => counts?.[state] ?? 0;
    return {
      all: of('active') + of('disabled') + of('expired') + of('upcoming'),
      active: of('active'), disabled: of('disabled'), expired: of('expired'), upcoming: of('upcoming'),
    };
  });
  readonly chips = computed(() =>
    FILTERS.map(option => ({ ...option, text: this.overview() ? `${option.label} ${this.chipCounts()[option.key]}` : option.label })),
  );
  /** The expired-but-enabled count behind the banner; 0 hides it, as does the 已過期 filter itself. */
  readonly banner = computed(() => {
    const count = this.overview()?.counts.enabled_expired ?? 0;
    return this.filter() === 'expired' ? 0 : count;
  });

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
