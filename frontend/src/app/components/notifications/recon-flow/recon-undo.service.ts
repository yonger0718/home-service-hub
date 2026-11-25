import { DestroyRef, Injectable, Signal, inject, signal } from '@angular/core';

import { AccountingService } from '../../../services/accounting.service';
import { ToastService } from '../../../services/toast.service';
import { inboxErrorMessage } from '../../accounting/inbox/inbox-view';
import { isNotRevertable } from '../../accounting/inbox/handled-list/handled-list';
import { InboxService } from '../../accounting/inbox/inbox.service';
import { StatementsService } from '../../accounting/statements/statements.service';

/**
 * A finished 復原. `caseId` is the answered case's id at answer time: after the reconcile the question comes back
 * under a new id, so a flow finds it by what it is about (`itemKey`), not by this id.
 */
export interface ReconReverted {
  readonly caseId: number;
  readonly statementId: number;
  readonly seq: number;
}

/** One applied reconciliation answer that 復原 can take back. */
export interface UndoOffer {
  readonly answer: string;
  readonly caseId: number;
  /** In apply order; reverted newest first. */
  readonly actionIds: readonly number[];
  readonly accountId: number;
  readonly statementId: number;
  /** The owner rule the answer taught (the apply's `rule_id`, the item's merchant pattern), if any. */
  readonly rule: { readonly id: number; readonly pattern: string } | null;
}

/**
 * 復原 for one applied reconciliation answer (plan ruling 8). Root-provided with the root injector's DestroyRef, so
 * the toast action outlives the flow component that offered it.
 *
 * Strictly in sequence: revert every action newest first (stop at the first failure); then switch off the rule the
 * answer taught (an enabled rule would repeat the undone answer at the next reconcile); then reconcile the statement,
 * because a revert reopens only the answered case and the questions it superseded come back only on a reconcile.
 * A failed rule switch skips the reconcile. Every outcome ends with `markEntriesChanged()`, a `reverted` bump and one
 * result toast replacing 復原中….
 */
@Injectable({ providedIn: 'root' })
export class ReconUndoService {
  private readonly inbox = inject(InboxService);
  private readonly statements = inject(StatementsService);
  private readonly accounting = inject(AccountingService);
  private readonly toasts = inject(ToastService).scope(inject(DestroyRef));
  private readonly revertedState = signal<ReconReverted | null>(null);

  /** Bumped after a 復原 finished (success or not): the answered case and its statement. */
  readonly reverted: Signal<ReconReverted | null> = this.revertedState.asReadonly();

  /** Raise 完成 with 復原 for one applied answer (no 復原 without an action id). */
  offer(offer: UndoOffer): void {
    const text = '已處理：' + offer.answer;
    if (offer.actionIds.length === 0) {
      this.toasts.done(text);
      return;
    }
    this.toasts.done(text, { undo: () => this.undo(offer) });
  }

  private undo(offer: UndoOffer): void {
    this.toasts.progress('復原中…');
    this.revert(offer, [...offer.actionIds].reverse());
  }

  /** Newest first, each request only after the previous one succeeded; stops at the first failure. */
  private revert(offer: UndoOffer, ids: readonly number[]): void {
    const [id, ...rest] = ids;
    this.inbox.revert(id).subscribe({
      next: () => (rest.length > 0 ? this.revert(offer, rest) : this.switchRuleOff(offer)),
      error: (err: unknown) => {
        const message = isNotRevertable(err) ? '之後已有其他變更，無法復原' : inboxErrorMessage(err);
        this.finish(offer, 'error', '復原沒成功：' + message);
      },
    });
  }

  private switchRuleOff(offer: UndoOffer): void {
    const rule = offer.rule;
    if (rule === null) {
      this.reconcile(offer, '已復原');
      return;
    }
    this.inbox.patchRule(rule.id, false).subscribe({
      next: () => this.reconcile(offer, `已復原，並停用「${rule.pattern}」的規則`),
      // No reconcile: it would run the still-enabled rule and repeat the undone answer.
      error: () =>
        this.finish(
          offer,
          'error',
          `已復原，但「${rule.pattern}」的規則沒停用，下次對帳可能又照做；請到記帳設定關掉`,
        ),
    });
  }

  private reconcile(offer: UndoOffer, doneText: string): void {
    const notRederived = '已復原，但帳單沒有重新對過，相關的題目可能晚一點才出現';
    this.statements.reconcile(offer.accountId, offer.statementId).subscribe({
      // A 200 with `skipped` (parse_review, no_current_revision, retired) re-derived nothing either.
      next: (result) => this.finish(offer, result?.skipped ? 'error' : 'done', result?.skipped ? notRederived : doneText),
      error: () => this.finish(offer, 'error', notRederived),
    });
  }

  private finish(offer: UndoOffer, kind: 'done' | 'error', text: string): void {
    this.accounting.markEntriesChanged();
    this.revertedState.update((v) => ({ caseId: offer.caseId, statementId: offer.statementId, seq: (v?.seq ?? 0) + 1 }));
    if (kind === 'done') {
      this.toasts.done(text);
    } else {
      this.toasts.error(text);
    }
  }
}
