import { Injectable, Signal, inject } from '@angular/core';

import { NotifSummary } from '../components/notifications/notif-task';
import { NavBadge } from '../components/shell/navigation';
import { NotificationCenterService } from './notification-center.service';

export { BADGE_REFRESH_MIN_MS } from './notification-center.service';

/**
 * The nav's view of `NotificationCenterService` (R21, P7): its dots and its count. Kept as a facade so the nav
 * components and their specs stub one small service instead of the centre (which pulls in `HttpClient`).
 */
@Injectable({ providedIn: 'root' })
export class NavBadgeService {
  private readonly centre = inject(NotificationCenterService);

  readonly badges: Signal<Readonly<Record<string, NavBadge>>> = this.centre.badges;
  readonly summary: Signal<NotifSummary> = this.centre.summary;

  /** First round and the focus / visible refreshes. Idempotent. */
  start(): void {
    this.centre.start();
  }

  /** A throttled refresh, as a window focus asks for it. */
  refresh(): void {
    this.centre.refresh({ reason: 'focus' });
  }
}
