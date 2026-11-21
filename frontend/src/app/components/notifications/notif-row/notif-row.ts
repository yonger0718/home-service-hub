import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { BtnComponent } from '../../ui/btn/btn';
import {
  NotifCommand,
  NotifCommandAction,
  NotifLink,
  NotifTask,
  SOURCE_TAGS,
  isCommand,
} from '../notif-task';

/** The native control of a row's primary action: the inner `<button>` of `app-btn`, or the link (R28). */
export const NOTIF_PRIMARY_CONTROL = 'app-btn.notif-primary button, a.notif-primary';

/** One notification task: icon tile, title / tag / amount, when · meta, optional progress bar, actions. */
@Component({
  selector: 'app-notif-row',
  imports: [BtnComponent, RouterLink],
  templateUrl: './notif-row.html',
  styleUrl: './notif-row.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'notif-row',
    '[attr.data-task-id]': 'task().id',
    '[class.notif-row--alert]': 'alert()',
  },
})
export class NotifRowComponent {
  readonly task = input.required<NotifTask>();
  readonly busy = input(false);
  /** Phone 已逾期: the primary action is solid. */
  readonly solid = input(false);
  readonly run = output<NotifCommand>();

  protected readonly alert = computed(() => ['overdue', 'system'].includes(this.task().tier));
  protected readonly tag = computed(() => SOURCE_TAGS[this.task().source]);
  protected readonly isPi = computed(() => /^pi[\s-]/.test(this.task().icon));
  protected readonly piClass = computed(() => {
    const icon = this.task().icon;
    return icon.startsWith('pi ') ? icon : `pi ${icon}`;
  });
  /** Width of the bar fill, 0–100; null without progress or when the total is not positive. */
  protected readonly barWidth = computed(() => {
    const p = this.task().progress;
    return p && p.n > 0 ? `${Math.min(100, Math.max(0, (p.k / p.n) * 100))}%` : null;
  });
  protected readonly command = computed<NotifCommandAction | null>(() => {
    const primary = this.task().primary;
    return isCommand(primary) ? primary : null;
  });
  protected readonly link = computed<NotifLink | null>(() => {
    const primary = this.task().primary;
    return isCommand(primary) ? null : primary;
  });

  /** `label：title`: one accessible name per action, distinct between rows. */
  protected name(label: string): string {
    return `${label}：${this.task().title}`;
  }
}
