import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { RewardAccrualState } from '../../../models/accounting.model';

export const REWARD_STATE_LABELS: Record<RewardAccrualState, string> = {
  estimated: '預估',
  posted: '已入帳',
  bank_confirmed: '銀行確認',
  forfeited: '未獲得',
};

const REWARD_STATE_ICON_PATHS: Record<RewardAccrualState, string> = {
  estimated: 'M12 7v5l3 2 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  posted: 'M5 12l4 4 10-10',
  bank_confirmed: 'M4 12l4 4 6-6 M10 16l1 1 9-9',
  forfeited: 'M18 6L6 18 M6 6l12 12',
};

@Component({
  selector: 'app-reward-state-tag',
  template: `<span class="rst" [attr.data-state]="state()"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path [attr.d]="path()" /></svg>{{ label() }}</span>`,
  styleUrl: './reward-state-tag.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RewardStateTagComponent {
  readonly state = input.required<RewardAccrualState>();
  protected readonly label = computed(() => REWARD_STATE_LABELS[this.state()]);
  protected readonly path = computed(() => REWARD_STATE_ICON_PATHS[this.state()]);
}
