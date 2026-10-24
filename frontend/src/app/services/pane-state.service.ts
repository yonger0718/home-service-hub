import { Injectable, signal } from '@angular/core';

/** Right-pane state, written by layouts that open a right pane and read by the shell (sidebar auto-collapse, FAB). */
@Injectable({ providedIn: 'root' })
export class PaneStateService {
  /** A right pane is open in 'panes' mode (≥ 1024 px). */
  readonly paneOpen = signal(false);
  /** The entry form (new / edit) is on screen. */
  readonly formOpen = signal(false);
  /** The 760–1023 px modal detail sheet is open. */
  readonly sheetOpen = signal(false);
  /** CSS length / calc() for the floating button's `right`; null = the shell default. */
  readonly fabRight = signal<string | null>(null);
}
