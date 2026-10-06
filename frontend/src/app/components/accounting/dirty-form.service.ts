// frontend/src/app/components/accounting/dirty-form.service.ts
import { Injectable, signal } from '@angular/core';

/** A form that can hold unsaved input (the entry form). */
export interface DirtyAware {
  isDirty(): boolean;
  showDiscardPrompt(): void;
}

/**
 * The one guard for every exit of the entry form in both layouts (spec §4.3): layout ✕ / backdrop / swipe / Esc and
 * the form's own ✕ / Esc / shortcut call `requestClose(run)`. A clean (or no) form closes at once; a dirty one shows
 * its 放棄 strip and keeps `run` pending until 放棄 (`confirmDiscard`) or 留下 (`cancelDiscard`).
 */
@Injectable({ providedIn: 'root' })
export class DirtyFormRegistry {
  private form: DirtyAware | null = null;
  private pending: (() => void) | null = null;
  private readonly prompting = signal(false);
  /** True while the registered form should show its 放棄未儲存的內容？ strip. */
  readonly promptOpen = this.prompting.asReadonly();

  register(form: DirtyAware): void {
    this.form = form;
    this.clearPending();
  }

  /** Without an argument, or with the registered form: forget it and any pending close. */
  unregister(form?: DirtyAware): void {
    if (form && form !== this.form) {
      return;
    }
    this.form = null;
    this.clearPending();
  }

  requestClose(run: () => void): void {
    const form = this.form;
    if (!form || !form.isDirty()) {
      this.clearPending();
      run();
      return;
    }
    this.pending = run;
    this.prompting.set(true);
    form.showDiscardPrompt();
  }

  /** 放棄: run the pending close. */
  confirmDiscard(): void {
    const run = this.pending;
    this.clearPending();
    run?.();
  }

  /** 留下: keep the draft. */
  cancelDiscard(): void {
    this.clearPending();
  }

  /** A save (or save-and-continue) succeeded: nothing is left to discard. */
  clearPending(): void {
    this.pending = null;
    this.prompting.set(false);
  }
}
