import { Injectable, signal } from '@angular/core';

/** Accounting pane state, written by the accounting layout and read by the shell (sidebar auto-collapse, FAB). */
@Injectable({ providedIn: 'root' })
export class PaneStateService {
  /** A right pane is open in 'panes' mode (≥ 1024 px). */
  readonly paneOpen = signal(false);
  /** The entry form (new / edit) is on screen. */
  readonly formOpen = signal(false);
  /** The 760–1023 px modal detail sheet is open. */
  readonly sheetOpen = signal(false);
}
