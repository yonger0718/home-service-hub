import { LocationStrategy } from '@angular/common';
import { Injectable, computed, inject } from '@angular/core';
import { Params, Router } from '@angular/router';

import { ENTRY_FORM_PATH } from '../accounting/keyboard-shortcuts';
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

  /** A link click: on the new-entry form itself it does nothing; a modified click (new tab / window) is left to the browser. */
  open(event: MouseEvent): void {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    if (this.onNewEntryForm()) {
      return;
    }
    this.go();
  }

  /** Opens the entry form (dirty-form check first), e.g. for the N key. */
  go(): void {
    this.registry.requestClose(() => void this.router.navigate(ENTRY_FORM, { queryParams: this.calendarQuery() }));
  }

  /** The new-entry form (not the edit form of an entry): the link would lead to the page already open. */
  private onNewEntryForm(): boolean {
    const path = this.router.url.split(/[?#]/)[0];
    return ENTRY_FORM_PATH.test(path) && path === ENTRY_FORM[0];
  }

  private calendarQuery(): Params {
    return timelineCalendarParams(this.router, this.router.url);
  }
}
