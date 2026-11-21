import { DestroyRef, Injectable, Signal, inject, signal } from '@angular/core';

export type ToastKind = 'done' | 'progress' | 'error';
export interface ToastAction {
  readonly label: '復原' | '重試';
  readonly run: () => void;
}
export interface Toast {
  readonly id: number;
  readonly kind: ToastKind;
  readonly text: string;
  readonly action: ToastAction | null;
}
export type ToastPause = 'hover' | 'focus';

export const TOAST_DONE_MS = 5000;

/** `summary` alone when `detail` is empty, else `${summary}：${detail}`. */
export function toastText(summary: string, detail?: string | null): string {
  return detail ? `${summary}：${detail}` : summary;
}

/** What a component holds: every toast it raises is owned by its DestroyRef. */
export interface ToastScope {
  done(text: string, options?: { readonly undo?: () => void }): number;
  progress(text: string): number;
  error(text: string, options?: { readonly retry?: () => void }): number;
  /** Dismisses `id` only if it is still the current toast. */
  dismiss(id: number): void;
}

/** `inject(ToastService).scope(inject(DestroyRef))`; call in an injection context. */
export function injectToasts(): ToastScope {
  return inject(ToastService).scope(inject(DestroyRef));
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  private readonly state = signal<Toast | null>(null);
  readonly current: Signal<Toast | null> = this.state.asReadonly();

  private nextId = 1;
  /** The callback behind the current toast's action; the Toast itself only carries a runner. */
  private callback: (() => void) | null = null;
  private unregisterOwner: (() => void) | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private deadline = 0;
  private remaining = TOAST_DONE_MS;
  private readonly held = new Set<ToastPause>();

  /**
   * A scope for `owner`. While the owner lives its toasts carry their action; on its destroy its progress toast is
   * dismissed and its done / error toast keeps its text and loses its action. After the owner is destroyed progress()
   * returns -1 and raises nothing (no 進行中 for a page that is gone), but done() / error() still show their result
   * (a late answer is real news), without undo / retry: the page that could run them no longer exists.
   * Replacing a toast unregisters the hook.
   */
  scope(owner: DestroyRef): ToastScope {
    const raise = (kind: ToastKind, text: string, label: ToastAction['label'] | null, run?: () => void): number => {
      if (owner.destroyed) {
        return kind === 'progress' ? -1 : this.raise(kind, text, null);
      }
      const id = this.raise(kind, text, label && run ? label : null, run);
      const unregister = owner.onDestroy(() => this.ownerGone(id, kind));
      this.unregisterOwner = unregister;
      return id;
    };
    return {
      done: (text, options) => raise('done', text, '復原', options?.undo),
      progress: (text) => raise('progress', text, null),
      error: (text, options) => raise('error', text, '重試', options?.retry),
      dismiss: (id) => this.dismiss(id),
    };
  }

  /** Dismisses `id` only if it is still current (the outlet's ✕ / Esc). */
  dismiss(id: number): void {
    if (this.state()?.id === id) {
      this.clear();
    }
  }

  /**
   * If `id` is current and has an action: dismiss(id) first, then run the captured callback once. Never touches a
   * toast the callback raises.
   */
  runAction(id: number): void {
    const toast = this.state();
    const run = this.callback;
    if (toast?.id !== id || !toast.action || !run) {
      return;
    }
    this.dismiss(id);
    run();
  }

  /** The timer runs only while no reason is held. */
  pause(reason: ToastPause): void {
    this.held.add(reason);
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
      this.remaining = Math.max(0, this.deadline - Date.now());
    }
  }

  resume(reason: ToastPause): void {
    if (!this.held.delete(reason) || this.held.size > 0) {
      return;
    }
    if (this.state()?.kind === 'done' && this.timer === null) {
      this.startTimer();
    }
  }

  private raise(kind: ToastKind, text: string, label: ToastAction['label'] | null, run?: () => void): number {
    this.stopTimer();
    this.unregisterOwner?.();
    this.unregisterOwner = null;
    // Pause reasons belong to the toast they were raised on: the outlet re-asserts the true ones for the new toast.
    this.held.clear();
    const id = this.nextId++;
    this.callback = label && run ? run : null;
    this.state.set({ id, kind, text, action: label && run ? { label, run: () => this.runAction(id) } : null });
    if (kind === 'done') {
      this.remaining = TOAST_DONE_MS;
      this.startTimer();
    }
    return id;
  }

  private ownerGone(id: number, kind: ToastKind): void {
    const toast = this.state();
    if (toast?.id !== id) {
      return;
    }
    this.unregisterOwner = null;
    if (kind === 'progress') {
      this.clear();
    } else {
      this.callback = null;
      this.state.set({ ...toast, action: null });
    }
  }

  private startTimer(): void {
    const id = this.state()?.id;
    this.deadline = Date.now() + this.remaining;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (id !== undefined) {
        this.dismiss(id);
      }
    }, this.remaining);
  }

  private stopTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private clear(): void {
    this.stopTimer();
    this.unregisterOwner?.();
    this.unregisterOwner = null;
    this.callback = null;
    this.state.set(null);
    // A toast that vanishes under the pointer never gets its pointerleave.
    this.held.clear();
  }
}
