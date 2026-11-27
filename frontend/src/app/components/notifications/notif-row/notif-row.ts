import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { NOTIF_CLOCK } from '../../../services/notif-clock';
import { BtnComponent } from '../../ui/btn/btn';
import { PickerComponent, PickerOption } from '../../ui/picker/picker';
import {
  NotifCommand,
  NotifCommandAction,
  NotifLink,
  NotifTask,
  SOURCE_TAGS,
  isCommand,
} from '../notif-task';
import { SNOOZE_OPTIONS, SnoozeOption, snoozeChoices } from '../snooze';

/** The native control of a row's primary action: the inner `<button>` of `app-btn`, or the link (R28). */
export const NOTIF_PRIMARY_CONTROL = 'app-btn.notif-primary button, a.notif-primary';

/** One notification task: icon tile, title / tag / amount, when · meta, optional progress bar, actions. */
@Component({
  selector: 'app-notif-row',
  imports: [BtnComponent, PickerComponent, RouterLink],
  templateUrl: './notif-row.html',
  styleUrl: './notif-row.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'notif-row',
    '[attr.data-task-id]': 'task().id',
    '[class.notif-row--alert]': 'alert()',
    '[class.notif-row--selected]': 'selected()',
  },
})
export class NotifRowComponent {
  readonly task = input.required<NotifTask>();
  readonly busy = input(false);
  /** Phone 已逾期: the primary action is solid. */
  readonly solid = input(false);
  /**
   * The statement open in the review flow beside the list: the row is highlighted and its primary reads 對帳中 with
   * `aria-current="true"` (still a link; pressing it again is harmless).
   */
  readonly selected = input(false);
  /** Offers 稍後提醒 (PR E2: every counted task). */
  readonly snoozable = input(true);
  readonly run = output<NotifCommand>();
  readonly snooze = output<SnoozeOption>();

  private readonly clock = inject(NOTIF_CLOCK);
  /** 稍後提醒's options, each hinted with the time it ends (taken when the row renders). */
  protected readonly snoozeOptions = computed<PickerOption[]>(() => {
    this.task();
    const now = this.clock();
    return snoozeChoices(now).map((c) => ({ value: c.value, label: c.label, hint: c.hint }));
  });

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

  /** The primary's visible label: 對帳中 (確認中 for the e-invoice task) while the row is selected. */
  protected primaryLabel(label: string): string {
    return this.selected() ? (this.task().id === 'einvoice' ? '確認中' : '對帳中') : label;
  }

  protected onSnooze(value: string): void {
    const option = SNOOZE_OPTIONS.find((o) => o.value === value);
    if (option) {
      this.snooze.emit(option.value);
    }
  }

  /** `label：title`: one accessible name per action, distinct between rows. */
  protected name(label: string): string {
    return `${label}：${this.task().title}`;
  }
}
