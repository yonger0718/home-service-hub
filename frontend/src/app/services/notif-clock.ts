import { InjectionToken } from '@angular/core';

/** The notification centre's clock; tests provide a fixed one. */
export const NOTIF_CLOCK = new InjectionToken<() => Date>('NOTIF_CLOCK', { providedIn: 'root', factory: () => () => new Date() });
