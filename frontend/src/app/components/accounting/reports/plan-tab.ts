import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal, untracked } from '@angular/core';

import { CategoryNode, Project } from '../../../models/accounting.model';
import { BreakdownResponse, BudgetInput, BudgetStatus, BudgetStatusResponse } from '../../../models/reports.model';
import { daysBetween, shortDate } from '../dates';
import type { LoadState } from './reports';
import { ReportPeriod, daysInMonth, monthOf, n0, num, pct1, seriesColor } from './report-math';

interface BudgetBar {
  id: number;
  label: string;
  color: string;
  total: boolean;
  usedText: string;
  limitText: string;
  width: string;
  over: boolean;
  note: string;
}

interface Segment {
  label: string;
  amount: number;
  amountText: string;
  color: string;
}

interface ProjectCard {
  key: string;
  name: string;
  meta: string;
  tag: string;
  excluded: boolean;
  totalText: string;
  budgetText: string | null;
  segments: Segment[];
}

/** A row of the editor: an existing budget's draft amount. */
export interface BudgetAmount {
  id: number;
  amount: string;
}

/**
 * 專案與預算: each monthly budget as a bar with the month's time marker, the used and limit amounts, 剩 N or 超出 N;
 * each project as a card with its total, dates, budget use and category split (不計入報表 projects marked);
 * 編輯預算 edits amounts from the selected month on, adds and removes budgets.
 */
@Component({
  selector: 'app-report-plan',
  templateUrl: './plan-tab.html',
  styleUrl: './plan-tab.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReportPlanComponent {
  readonly status = input<BudgetStatusResponse | null>(null);
  readonly statusState = input<LoadState>('loading');
  /** Expense by project (split by category) over the projects' whole life, excluded projects included. */
  readonly projects = input<BreakdownResponse | null>(null);
  readonly projectsState = input<LoadState>('loading');
  /** The owner's projects (archived state, 不計入報表 flag) and expense roots, for the cards and the editor. */
  readonly projectList = input<Project[]>([]);
  readonly categories = input<CategoryNode[]>([]);
  readonly period = input.required<ReportPeriod>();
  readonly today = input.required<string>();
  /** A budget write is in flight / the last one failed. */
  readonly busy = input(false);
  readonly error = input<string | null>(null);

  readonly saveAmounts = output<BudgetAmount[]>();
  readonly addBudget = output<BudgetInput>();
  readonly removeBudget = output<number>();

  /** The budgets are monthly: a year or custom period shows its first month's. */
  readonly month = computed(() => monthOf(this.period().from));
  readonly title = computed(() => `${Number(this.month().slice(5, 7))} 月預算`);
  private readonly days = computed(() => daysInMonth(this.month()));
  private readonly elapsed = computed(() => Math.round((this.status()?.time_progress ?? 0) * this.days()));
  readonly subtitle = computed(() => `已過 ${this.elapsed()} / ${this.days()} 天 · 直線＝時間進度`);
  readonly markerLeft = computed(() => `${((this.status()?.time_progress ?? 0) * 100).toFixed(1)}%`);

  private readonly monthly = computed(() =>
    (this.status()?.budgets ?? []).filter(budget => budget.scope !== 'project').sort((a, b) => Number(a.scope === 'total') - Number(b.scope === 'total') || num(b.used) - num(a.used)),
  );

  readonly bars = computed<BudgetBar[]>(() =>
    this.monthly().map((budget, index) => {
      const used = num(budget.used);
      const limit = num(budget.limit);
      const total = budget.scope === 'total';
      const left = this.days() - this.elapsed();
      return {
        id: budget.id,
        label: budget.label,
        color: total ? 'var(--app-text)' : (budget.color ?? seriesColor(index + 1)),
        total,
        usedText: n0(used),
        limitText: n0(limit),
        width: `${Math.min(limit > 0 ? (used / limit) * 100 : 0, 100).toFixed(1)}%`,
        over: budget.over,
        note: budget.over ? `超出 ${n0(used - limit)}（${pct1(used, limit)}）` : `剩 ${n0(limit - used)}${left > 0 ? ` · 還有 ${left} 天` : ''}`,
      };
    }),
  );

  /**
   * Projects active in the period: entries on or before its last day and on or after its first. The API leaves
   * first / last date out above 50 groups and on 其他; unknown dates keep the card and drop the date line.
   */
  readonly cards = computed<ProjectCard[]>(() => {
    const period = this.period();
    const budgets = this.status()?.budgets ?? [];
    return (this.projects()?.rows ?? [])
      .filter(
        row =>
          row.key !== null &&
          num(row.amount) > 0 &&
          (row.first_date == null || row.first_date <= period.to) &&
          (row.last_date == null || row.last_date >= period.from),
      )
      .map(row => {
        const project = this.projectList().find(p => String(p.id) === row.key);
        const budget = budgets.find(b => b.scope === 'project' && String(b.project_id) === row.key);
        const total = num(row.amount);
        const first = row.first_date;
        const last = row.last_date;
        const finished = !!project?.is_archived || (last != null && last < this.today());
        const days = first != null && last != null ? daysBetween(first, last) + 1 : 0;
        const dates =
          first == null || last == null
            ? []
            : [finished ? `${shortDate(first)} – ${shortDate(last)} · ${days} 天 · 日均 ${n0(total / days)}` : `${shortDate(first)} 起 · 進行中`];
        const meta = [...dates, ...(project?.exclude_from_reports ? ['不計入月報'] : [])].join(' · ');
        const splits = (row.splits ?? []).filter(split => num(split.amount) > 0);
        return {
          key: row.key!,
          name: row.label,
          meta,
          tag: finished ? '已結束' : '進行中',
          excluded: !!project?.exclude_from_reports,
          totalText: `$${n0(total)}`,
          budgetText: budget ? `預算 ${n0(budget.limit)} · 用了 ${pct1(num(budget.used), num(budget.limit))}` : null,
          segments: splits.map((split, index) => ({ label: split.label, amount: num(split.amount), amountText: n0(split.amount), color: seriesColor(index) })),
        };
      });
  });

  // ---- editor ---------------------------------------------------------------------------------------------------

  readonly editing = signal(false);
  /** Draft amounts by budget id while editing. */
  readonly drafts = signal<Record<number, string>>({});
  readonly newScope = signal('');
  readonly newAmount = signal('');

  /** What a new budget may be about: 全部支出 once, each expense root and each live project without one. */
  readonly candidates = computed(() => {
    const budgets = this.status()?.budgets ?? [];
    const options: { value: string; label: string }[] = [];
    if (!budgets.some(b => b.scope === 'total')) options.push({ value: 'total', label: '全部支出' });
    for (const root of this.categories().filter(c => !c.is_hidden && !budgets.some(b => b.scope === 'category' && b.category_id === c.id))) {
      options.push({ value: `category:${root.id}`, label: root.name });
    }
    for (const project of this.projectList().filter(p => !p.is_archived && !budgets.some(b => b.scope === 'project' && b.project_id === p.id))) {
      options.push({ value: `project:${project.id}`, label: `專案 · ${project.name}` });
    }
    return options;
  });

  constructor() {
    // A fresh status (after a write or another month) resets the drafts to what is on the server.
    effect(() => {
      const status = this.status();
      untracked(() => this.drafts.set(Object.fromEntries((status?.budgets ?? []).map(b => [b.id, n0(b.limit).replaceAll(',', '')]))));
    });
  }

  startEditing(): void {
    this.editing.set(true);
  }

  cancel(): void {
    this.editing.set(false);
    this.newScope.set('');
    this.newAmount.set('');
  }

  setDraft(id: number, value: string): void {
    this.drafts.update(drafts => ({ ...drafts, [id]: value }));
  }

  /** Every budget whose draft differs from its limit, when all drafts are positive numbers. */
  changed(): BudgetAmount[] {
    const drafts = this.drafts();
    return (this.status()?.budgets ?? [])
      .filter(b => drafts[b.id] !== undefined && Number(drafts[b.id]) !== num(b.limit))
      .map(b => ({ id: b.id, amount: String(Number(drafts[b.id])) }));
  }

  draftsValid(): boolean {
    return Object.values(this.drafts()).every(value => Number(value) > 0);
  }

  save(): void {
    if (!this.draftsValid()) return;
    const changed = this.changed();
    if (changed.length) this.saveAmounts.emit(changed);
    this.editing.set(false);
  }

  add(): void {
    const scope = this.newScope();
    const amount = Number(this.newAmount());
    if (!scope || !(amount > 0)) return;
    const [kind, id] = scope.split(':');
    this.addBudget.emit({
      scope: kind as BudgetInput['scope'],
      category_id: kind === 'category' ? Number(id) : null,
      project_id: kind === 'project' ? Number(id) : null,
      amount: String(amount),
      effective_from: this.month(),
    });
    this.newScope.set('');
    this.newAmount.set('');
  }

  remove(budget: Pick<BudgetStatus, 'id'> | BudgetBar): void {
    this.removeBudget.emit(budget.id);
  }

  /** The project budgets, editable in the same list. */
  readonly projectBudgets = computed(() => (this.status()?.budgets ?? []).filter(b => b.scope === 'project'));
}
