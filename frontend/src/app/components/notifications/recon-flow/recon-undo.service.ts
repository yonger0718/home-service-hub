import { DestroyRef, Injectable, Signal, inject, signal } from '@angular/core';

import { AccountingService } from '../../../services/accounting.service';
import { ToastService } from '../../../services/toast.service';
import { inboxErrorMessage } from '../../accounting/inbox/inbox-view';
import { isNotRevertable } from '../../accounting/inbox/handled-list/handled-list';
import { InboxService } from '../../accounting/inbox/inbox.service';

/**
 * 復原 for one applied reconciliation answer. Root-provided with the root injector's DestroyRef, so the toast action
 * outlives the flow component that offered it.
 */
@Injectable({ providedIn: 'root' })
export class ReconUndoService {
  private readonly inbox = inject(InboxService);
  private readonly accounting = inject(AccountingService);
  private readonly toasts = inject(ToastService).scope(inject(DestroyRef));
  private readonly revertedState = signal<{ readonly caseId: number; readonly seq: number } | null>(null);

  /** Bumped after a 復原 finished (success or not): the case to show again. */
  readonly reverted: Signal<{ readonly caseId: number; readonly seq: number } | null> =
    this.revertedState.asReadonly();

  /** Raise 完成 with 復原 for one applied answer. `actionIds` in apply order; `rulePattern` when the answer taught a rule. */
  offer(answer: string, caseId: number, actionIds: readonly number[], rulePattern: string | null): void {
    const text = '已處理：' + answer;
    if (actionIds.length === 0) {
      this.toasts.done(text);
      return;
    }
    this.toasts.done(text, { undo: () => this.undo(caseId, [...actionIds].reverse(), rulePattern) });
  }

  /** Newest first, each request only after the previous one succeeded; stops at the first failure. */
  private undo(caseId: number, ids: readonly number[], rulePattern: string | null): void {
    const [id, ...rest] = ids;
    this.inbox.revert(id).subscribe({
      next: () => {
        if (rest.length > 0) {
          this.undo(caseId, rest, rulePattern);
          return;
        }
        this.finish(caseId);
        this.toasts.done(
          rulePattern === null ? '已復原' : `已復原。「${rulePattern}」的規則還在，可在記帳設定關掉`,
        );
      },
      error: (err: unknown) => {
        this.finish(caseId);
        const message = isNotRevertable(err) ? '之後已有其他變更，無法復原' : inboxErrorMessage(err);
        this.toasts.error('復原沒成功：' + message);
      },
    });
  }

  private finish(caseId: number): void {
    this.accounting.markEntriesChanged();
    this.revertedState.update((v) => ({ caseId, seq: (v?.seq ?? 0) + 1 }));
  }
}
