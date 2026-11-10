import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { RewardRule, RewardRuleState, RewardUsage } from '../../../../models/accounting.model';
import { CapBarComponent } from '../../../ui/cap-bar/cap-bar';
import { SwitchComponent } from '../../../ui/switch/switch';
import {
  RULE_STATE_LABELS, RewardUnit, mutedLine, postingSummary, rateChip, ruleState, ruleTitle, usageUnit,
} from '../../reward-rule-text';

export interface RuleRowView {
  rule: RewardRule;
  state: RewardRuleState;
  title: string;
  rate: string;
  /** The state tag on a non-active rule; null on an active one. */
  tag: string | null;
  /** 基本 on an active basic rule only — one tag per row, the state wins. */
  basic: boolean;
  posting: string;
  /** The usage row behind the cap bar — active rules only. */
  usage: RewardUsage | null;
  unit: RewardUnit | null;
  /** The muted line in place of the bar on a non-active rule. */
  muted: string | null;
}

/** The account settings 回饋規則 rows (D6): handle, name + rate chip + tags, posting summary, cap bar or muted line,
 *  enable switch and ⋯. Presentational: tapping the row emits `open`, the switch emits `toggle`; the page writes. */
@Component({
  selector: 'app-account-rules',
  imports: [CapBarComponent, SwitchComponent],
  templateUrl: './rules-section.html',
  styleUrl: './rules-section.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountRulesComponent {
  readonly rules = input.required<RewardRule[]>();
  /** Rule id → the usage row shown on that rule (a shared-cap group already collapsed to its first listed member). */
  readonly usage = input<ReadonlyMap<number, RewardUsage>>(new Map());
  /** `YYYY-MM-DD`. */
  readonly today = input.required<string>();
  readonly busy = input(false);

  readonly open = output<RewardRule>();
  readonly toggle = output<{ rule: RewardRule; enabled: boolean }>();
  readonly reorder = output<number[]>();
  readonly remove = output<RewardRule>();

  protected readonly rows = computed<RuleRowView[]>(() => {
    const today = this.today();
    const usage = this.usage();
    return this.rules().map((rule): RuleRowView => {
      const state = ruleState(rule, today);
      const active = state === 'active';
      const use = active ? usage.get(rule.id) ?? null : null;
      return {
        rule,
        state,
        title: ruleTitle(rule),
        rate: rateChip(rule),
        tag: active ? null : RULE_STATE_LABELS[state],
        basic: active && rule.is_basic,
        posting: postingSummary(rule),
        usage: use,
        unit: use ? usageUnit(use) : null,
        muted: mutedLine({ state, starts_on: rule.starts_on }),
      };
    });
  });
}
