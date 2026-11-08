import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { EntryDetail } from '../../../models/accounting.model';
import { RewardStateTagComponent } from '../../ui/reward-state-tag/reward-state-tag';
import { REWARD_POSTED } from '../review-reason';
import { RewardAmountText, rewardCards } from './reward-cards';

/** The entry detail's 回饋 section (D7): one card per accrual or reward row, then the projected reprice card. */
@Component({
  selector: 'app-entry-reward-section',
  standalone: true,
  imports: [RouterLink, RewardStateTagComponent],
  templateUrl: './reward-section.html',
  styles: `
    .dsec { color: var(--app-text-muted); font-size: 0.78rem; font-weight: 800; margin: 12px 0 4px; }
    .reward-card { border-bottom: 1px solid var(--app-border); display: flex; flex-direction: column; font-size: 0.88rem; gap: 2px; padding: 8px 0; }
    .reward-head { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
    .reward-amount { color: var(--tone-pos, var(--c-green)); font-variant-numeric: tabular-nums; font-weight: 800; }
    s.reward-amount { color: var(--app-text-muted); }
    .reward-was { color: var(--app-text-muted); font-size: 0.8rem; margin-left: 6px; }
    .reward-note { color: var(--app-text-muted); font-size: 0.8rem; margin: 0; }
    .reward-link { align-items: center; display: inline-flex; font-size: 0.85rem; min-height: 44px; }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppEntryRewardSectionComponent {
  readonly detail = input.required<EntryDetail>();
  readonly amountText = input.required<RewardAmountText>();
  readonly today = input.required<string>();

  readonly cards = computed(() => rewardCards(this.detail(), this.today(), this.amountText()));
  /** A reward_posted entry keeps the section even without cards, so the reprice card never loses its host. */
  readonly visible = computed(() => this.cards().length > 0 || this.detail().review_reason === REWARD_POSTED);
}
