import { LocationStrategy } from '@angular/common';
import { Injectable, computed, inject } from '@angular/core';
import { Params, Router } from '@angular/router';

import { DirtyFormRegistry } from '../accounting/dirty-form.service';
import { timelineCalendarParams } from '../accounting/timeline/calendar-url';
import { NavigationStateService } from './navigation-state.service';

const ENTRY_FORM: ['/accounting/entry'] = ['/accounting/entry'];

/**
 * 記一筆: the one way into the entry form from the shell's controls (floating button, sidebar link) and the N key.
 * A dirty form is asked first; the calendar month / day of the page on screen is carried along.
 */
@Injectable({ providedIn: 'root' })
export class EntryFormLauncher {
  private readonly router = inject(Router);
  private readonly registry = inject(DirtyFormRegistry);
  private readonly nav = inject(NavigationStateService);
  private readonly locationStrategy = inject(LocationStrategy);

  /**
   * The link's href, built like RouterLink's (base href included) with the calendar month / day of the current URL, so
   * a modified click (new tab) keeps them too; plain clicks go through `open`.
   */
  readonly href = computed(() =>
    this.locationStrategy.prepareExternalUrl(
      this.router.serializeUrl(this.router.createUrlTree(ENTRY_FORM, { queryParams: this.nav.calendarQuery() })),
    ),
  );

  /** A link click: a modified click (new tab / window) is left to the browser. */
  open(event: MouseEvent): void {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    this.go();
  }

  /** Opens the entry form (dirty-form check first), e.g. for the N key. */
  go(): void {
    this.registry.requestClose(() => void this.router.navigate(ENTRY_FORM, { queryParams: this.calendarQuery() }));
  }

  private calendarQuery(): Params {
    return timelineCalendarParams(this.router, this.router.url);
  }
}
