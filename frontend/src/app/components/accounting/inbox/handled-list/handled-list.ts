import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  output,
  signal,
  untracked,
} from '@angular/core';

import { AccountingService } from '../../../../services/accounting.service';
import { slashDate } from '../../dates';
import { formatNumber } from '../../format';
import { apiErrorText, splitApiCode } from '../../statements/statement-view';
import { actionLabel, inboxErrorMessage } from '../inbox-view';
import { ReconciliationAction, ReconciliationActionLine } from '../inbox.model';
import { InboxService } from '../inbox.service';

/** Rows the list shows (spec §7.3: the last 50 actions). */
export const HANDLED_LIMIT = 50;

const NOT_REVERTABLE = 'not_revertable';
const RULE_PREFIX = 'rule:';

export interface HandledRow {
  action: ReconciliationAction;
  /** `規則 #7 · 忽略 · 2026/10/03 全聯 −120 · 2026/10/20` (the line part only when the case has one). */
  text: string;
  /** The owner rule that answered this case (`rule:<id>` label), else null. */
  ruleId: number | null;
  revertable: boolean;
}

/** The rule id of a `rule:<id>` audit label. */
function ruleIdOf(label: string | null): number | null {
  if (!label?.startsWith(RULE_PREFIX)) {
    return null;
  }
  const id = Number(label.slice(RULE_PREFIX.length));
  return Number.isInteger(id) && id > 0 ? id : null;
}

/**
 * `2026/10/03 全聯 −120 · `: the case's bank line. The action rows carry no currency, so the amount is the grouped
 * number without a symbol (`formatNumber` with no currency: up to 2 decimals), `+` on money in like `{ sign: true }`.
 */
function lineText(line: ReconciliationActionLine | null | undefined): string {
  if (!line) {
    return '';
  }
  const body = formatNumber(line.amount, '');
  const amount = Number(line.amount) > 0 && body !== '0' ? `+${body}` : body;
  return `${slashDate(line.date)} ${line.merchant} ${amount} · `;
}

export function handledRow(action: ReconciliationAction): HandledRow {
  const ruleId = ruleIdOf(action.label);
  const who = action.label?.startsWith(RULE_PREFIX)
    ? `規則 #${action.label.slice(RULE_PREFIX.length)}`
    : action.actor === 'policy'
      ? '系統'
      : '你';
  const answer = action.answer ?? actionLabel(action.action);
  return {
    action,
    text: `${who} · ${answer} · ${lineText(action.line)}${slashDate(action.created_at)}`,
    ruleId,
    // A `revert` row is never revertable (the engine always refuses it).
    revertable:
      action.reverted_by_action_id === null && action.status === 'applied' && action.action !== 'revert',
  };
}

/** A 422 whose code is `not_revertable` (a string detail, or FastAPI's `detail[].msg`). */
function isNotRevertable(err: unknown): boolean {
  if (!(err instanceof HttpErrorResponse)) {
    return false;
  }
  const text = apiErrorText(err);
  if (text) {
    return splitApiCode(text).code === NOT_REVERTABLE;
  }
  const detail = (err.error as { detail?: unknown } | null)?.detail;
  return (
    Array.isArray(detail) &&
    (detail as { msg?: unknown }[]).some(
      (item) => typeof item?.msg === 'string' && splitApiCode(item.msg).code === NOT_REVERTABLE,
    )
  );
}

/**
 * 已處理 (spec §7.3): the last 50 reconciliation actions with who answered and how, and 撤銷 on the ones that can
 * still be undone. A reverted rule row offers 同時停用這條規則. Re-reads whenever the inbox service writes.
 */
@Component({
  selector: 'app-inbox-handled-list',
  standalone: true,
  templateUrl: './handled-list.html',
  styles: `
    :host {
      display: block;
      font-size: 14px;
      color: var(--app-text);
    }
    .handled-rows {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .handled-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px 8px;
      padding: 8px 0;
      border-bottom: 1px solid var(--app-border);
    }
    .what {
      flex: 1 1 12rem;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .handled-error,
    .load-error {
      flex-basis: 100%;
      font-size: 13px;
      color: var(--tone-neg, var(--c-red));
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppInboxHandledListComponent {
  private readonly inbox = inject(InboxService);
  private readonly accounting = inject(AccountingService);

  /** After a successful 撤銷. */
  readonly changed = output<void>();

  private readonly actions = signal<ReconciliationAction[]>([]);
  readonly loadFailed = signal(false);
  /** Inline message per action id (不能撤銷, or the mapped failure). */
  readonly errors = signal<ReadonlyMap<number, string>>(new Map());
  /** Reverted rule rows that still offer 同時停用這條規則, by action id. */
  readonly offerDisable = signal<ReadonlySet<number>>(new Set());
  private readonly busy = signal<ReadonlySet<number>>(new Set());
  private requestId = 0;

  readonly rows = computed(() => this.actions().map(handledRow));

  constructor() {
    // Runs once on init, then again after every write through the inbox service (answers, 撤銷, rule changes).
    effect(() => {
      this.inbox.changed();
      untracked(() => this.load());
    });
  }

  isBusy(id: number): boolean {
    return this.busy().has(id);
  }

  revert(row: HandledRow): void {
    const id = row.action.id;
    if (this.isBusy(id)) {
      return;
    }
    this.setBusy(id, true);
    this.setError(id, null);
    this.inbox.revert(id).subscribe({
      next: () => {
        this.setBusy(id, false);
        if (row.ruleId !== null) {
          this.offerDisable.update((set) => new Set(set).add(id));
        }
        // Like the inbox panel: a revert may delete or restore entries, and the parent may be gone before an output.
        this.accounting.markEntriesChanged();
        this.changed.emit();
      },
      error: (err: unknown) => {
        this.setBusy(id, false);
        this.setError(id, isNotRevertable(err) ? '不能撤銷' : inboxErrorMessage(err));
      },
    });
  }

  /** 同時停用這條規則: the rule stops answering new lines; the reverted case stays open. */
  disableRule(row: HandledRow): void {
    const id = row.action.id;
    if (row.ruleId === null || this.isBusy(id)) {
      return;
    }
    this.setBusy(id, true);
    this.setError(id, null);
    this.inbox.patchRule(row.ruleId, false).subscribe({
      next: () => {
        this.setBusy(id, false);
        this.offerDisable.update((set) => {
          const next = new Set(set);
          next.delete(id);
          return next;
        });
      },
      error: (err: unknown) => {
        this.setBusy(id, false);
        this.setError(id, inboxErrorMessage(err));
      },
    });
  }

  private load(): void {
    const id = ++this.requestId;
    this.inbox.actions(HANDLED_LIMIT).subscribe({
      next: (actions) => {
        if (id === this.requestId) {
          this.actions.set(actions);
          this.loadFailed.set(false);
        }
      },
      error: () => {
        if (id === this.requestId) {
          this.loadFailed.set(true);
        }
      },
    });
  }

  private setBusy(id: number, busy: boolean): void {
    this.busy.update((set) => {
      const next = new Set(set);
      if (busy) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }

  private setError(id: number, message: string | null): void {
    this.errors.update((map) => {
      const next = new Map(map);
      if (message === null) {
        next.delete(id);
      } else {
        next.set(id, message);
      }
      return next;
    });
  }
}
