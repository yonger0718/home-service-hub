import {
  ChangeDetectionStrategy, Component, DestroyRef, ElementRef, Injector, OnInit, afterNextRender, computed, inject, input,
  output, signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable } from 'rxjs';

import {
  LedgerAccount, Project, RewardMethod, RewardPosting, RewardRule, RewardRuleInput, RoundingMode, ShareCapWith,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { focusables, isHandledKey, restoreOverlayFocus, trapFocus } from '../accounting-ui';
import { decimalText } from '../format';
import { fieldErrors, writeErrorMessage } from '../http-errors';
import { REWARD_ROUNDING_LABELS, basicOverlapRuleId, parseReferenced, ruleLabel, ruleToInput } from '../reward-rule-text';

/** The sheet's editable state; numbers stay text until `toInput()`. */
export interface RuleForm {
  name: string;
  method: RewardMethod;
  rate: string;
  fixed_amount: string;
  posting: RewardPosting;
  delay_days: string;
  post_month_offset: string;
  post_day: string;
  txn_rounding: RoundingMode;
  total_rounding: RoundingMode;
  total_cap: string;
  share_cap_with: ShareCapWith;
  is_basic: boolean;
  reward_account_id: number | null;
  reward_project_id: number | null;
  starts_on: string;
  ends_on: string;
  is_enabled: boolean;
  description: string;
}

/** Fields that render their own `small.err[data-field]`; any other 422 key goes to the message line. */
const FIELD_KEYS = new Set(['name', 'rate', 'fixed_amount', 'delay_days', 'post_month_offset', 'post_day', 'total_cap',
  'share_cap_with', 'reward_account_id', 'reward_project_id', 'starts_on', 'ends_on', 'description']);

const ROUNDING_ORDER: RoundingMode[] = ['round', 'floor', 'ceil', 'keep'];

function decimalOrNull(text: string): string | null {
  return text.replace(/,/g, '').trim() || null;
}

/** Whole numbers only; the range (0–2, 1–31, 0–365) is left to the server's 422. */
function isInteger(text: string): boolean {
  return /^[+-]?\d+$/.test(text.trim());
}

/** Editor sheet for one reward rule (spec §6.1): create (rule = null) or edit, delete, or 停用 a referenced rule. */
@Component({
  selector: 'app-reward-rule-sheet',
  standalone: true,
  templateUrl: './reward-rule-sheet.html',
  styleUrls: ['../sheet.scss', './reward-rule-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class RewardRuleSheetComponent implements OnInit {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly destroyRef = inject(DestroyRef);
  private readonly accounting = inject(AccountingService);
  private opener: HTMLElement | null = null;
  private initial = '';
  private shareTouched = false;

  readonly accountId = input.required<number>();
  readonly currency = input.required<string>();
  /** null = create. */
  readonly rule = input<RewardRule | null>(null);
  /** The account's other rules, for 與…共用上限. */
  readonly siblings = input<RewardRule[]>([]);
  /** Open accounts for 入帳帳戶; the card itself is preselected. */
  readonly accounts = input<LedgerAccount[]>([]);
  readonly projects = input<Project[]>([]);

  readonly saved = output<RewardRule>();
  readonly deleted = output<number>();
  readonly closed = output<void>();
  /** 留下 on the sheet's 放棄 strip (the page resolves a route-leave prompt it delegated here). */
  readonly discardStayed = output<void>();

  readonly form = signal<RuleForm>(this.blankForm());
  readonly errors = signal<Record<string, string>>({});
  readonly message = signal<string | null>(null);
  readonly referenced = signal(false);
  readonly confirmingDelete = signal(false);
  readonly confirmingDiscard = signal(false);
  readonly busy = signal(false);

  readonly roundingModes = ROUNDING_ORDER;
  readonly roundingLabels = REWARD_ROUNDING_LABELS;
  readonly ruleLabel = ruleLabel;

  readonly isDirty = computed(() => JSON.stringify(this.form()) !== this.initial);
  readonly shareOptions = computed(() => this.siblings().filter(s => s.id !== this.rule()?.id));
  readonly rewardAccounts = computed(() => {
    const open = this.accounts().filter(a => !a.is_archived);
    const extra = [this.accountId(), this.form().reward_account_id].filter(
      (id, i, ids): id is number => id !== null && ids.indexOf(id) === i && !open.some(a => a.id === id),
    );
    return [
      ...open.map(a => ({ id: a.id, name: a.name })),
      ...extra.map(id => ({ id, name: this.accounts().find(a => a.id === id)?.name ?? `#${id}` })),
    ];
  });
  readonly projectOptions = computed(() => {
    const current = this.form().reward_project_id;
    return this.projects().filter(p => !p.is_archived || p.id === current);
  });

  ngOnInit(): void {
    this.opener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    const rule = this.rule();
    if (rule) {
      const sharing = rule.shared_cap_id === null
        ? undefined
        : this.shareOptions().find(s => s.shared_cap_id === rule.shared_cap_id);
      this.form.set({
        name: rule.name,
        method: rule.method,
        rate: decimalText(rule.rate),
        fixed_amount: decimalText(rule.fixed_amount),
        posting: rule.posting,
        delay_days: String(rule.delay_days),
        post_month_offset: String(rule.post_month_offset),
        post_day: String(rule.post_day),
        txn_rounding: rule.txn_rounding,
        total_rounding: rule.total_rounding,
        total_cap: decimalText(rule.total_cap),
        share_cap_with: sharing?.id ?? null,
        is_basic: rule.is_basic,
        reward_account_id: rule.reward_account_id ?? this.accountId(),
        reward_project_id: rule.reward_project_id,
        starts_on: rule.starts_on ?? '',
        ends_on: rule.ends_on ?? '',
        is_enabled: rule.is_enabled,
        description: rule.description ?? '',
      });
    } else {
      this.form.set({ ...this.blankForm(), reward_account_id: this.accountId() });
    }
    this.initial = JSON.stringify(this.form());
    afterNextRender(() => {
      const dialog = this.dialog();
      if (dialog) focusables(dialog)[0]?.focus();
    }, { injector: this.injector });
  }

  set<K extends keyof RuleForm>(key: K, value: RuleForm[K]): void {
    this.form.update(form => ({ ...form, [key]: value }));
  }

  idOrNull(value: string): number | null {
    return value === '' ? null : Number(value);
  }

  setShare(value: string): void {
    this.shareTouched = true;
    const id = this.idOrNull(value);
    this.set('share_cap_with', id ?? (this.rule()?.shared_cap_id ? 'none' : null));
  }

  shareValue(): string {
    const value = this.form().share_cap_with;
    return typeof value === 'number' ? String(value) : '';
  }

  /** Ignored while a write is in flight, so its response still reaches `saved` / `deleted`. */
  cancel(): void {
    if (this.busy()) {
      return;
    }
    if (this.isDirty()) {
      this.confirmingDiscard.set(true);
      afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>('.discard-stay')?.focus(),
        { injector: this.injector });
      return;
    }
    this.close();
  }

  stay(): void {
    this.confirmingDiscard.set(false);
    this.discardStayed.emit();
  }

  close(): void {
    if (this.busy()) {
      return;
    }
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

  save(): void {
    if (this.busy()) {
      return;
    }
    const errors = this.validate();
    this.errors.set(errors);
    this.message.set(null);
    this.referenced.set(false);
    if (Object.keys(errors).length > 0) {
      return;
    }
    const rule = this.rule();
    const body = this.toInput();
    this.write(
      rule ? this.accounting.updateRewardRule(this.accountId(), rule.id, body)
        : this.accounting.createRewardRule(this.accountId(), body),
      err => {
        const fields = fieldErrors(err);
        const overlap = basicOverlapRuleId(err);
        if (Object.keys(fields).length > 0) {
          this.errors.set(fields);
          const other = Object.keys(fields).find(key => !FIELD_KEYS.has(key));
          this.message.set(other ? fields[other] : null);
        } else if (overlap !== null) {
          const name = this.siblings().find(s => s.id === overlap)?.name ?? `#${overlap}`;
          this.message.set(`與「${name}」的基本回饋期間重疊`);
        } else {
          this.message.set(writeErrorMessage(err));
        }
      },
    );
  }

  remove(): void {
    const rule = this.rule();
    if (!rule || this.busy()) {
      return;
    }
    if (!this.confirmingDelete()) {
      this.confirmingDelete.set(true);
      return;
    }
    this.confirmingDelete.set(false);
    this.message.set(null);
    this.busy.set(true);
    this.accounting.deleteRewardRule(this.accountId(), rule.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.busy.set(false);
        restoreOverlayFocus(this.opener, this.host.nativeElement);
        this.deleted.emit(rule.id);
      },
      error: err => {
        this.busy.set(false);
        const counts = parseReferenced(err);
        this.referenced.set(counts !== null);
        this.message.set(counts
          ? `此規則已有 ${counts.entries} 筆交易 / ${counts.rewards} 筆回饋引用，改為停用？`
          : writeErrorMessage(err));
      },
    });
  }

  disable(): void {
    const rule = this.rule();
    if (!rule || this.busy()) {
      return;
    }
    this.write(
      this.accounting.updateRewardRule(this.accountId(), rule.id, { ...ruleToInput(rule), is_enabled: false }),
      err => {
        this.referenced.set(false);
        this.message.set(writeErrorMessage(err));
      },
    );
  }

  /** Overlay contract (spec §3.1): Esc closes only this sheet (asking first when dirty), marked handled; Tab stays inside. */
  onHostKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      if (this.confirmingDiscard()) {
        this.stay();
      } else {
        this.cancel();
      }
      return;
    }
    const dialog = this.dialog();
    if (event.key === 'Tab' && dialog) {
      trapFocus(dialog, event);
    }
  }

  private write(request: Observable<RewardRule>, onError: (err: unknown) => void): void {
    this.busy.set(true);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: rule => {
        this.busy.set(false);
        restoreOverlayFocus(this.opener, this.host.nativeElement);
        this.saved.emit(rule);
      },
      error: err => {
        this.busy.set(false);
        onError(err);
      },
    });
  }

  /** Mirrors `RewardRuleIn` and the service's cross-field checks, so the common mistakes never round-trip. */
  private validate(): Record<string, string> {
    const form = this.form();
    const errors: Record<string, string> = {};
    if (!form.name.trim()) {
      errors['name'] = '請輸入名稱';
    }
    if (form.method === 'percent') {
      const rate = Number(decimalOrNull(form.rate) ?? NaN);
      if (!(rate > 0 && rate <= 100)) {
        errors['rate'] = '比例須大於 0 且不超過 100';
      }
    } else if (!(Number(decimalOrNull(form.fixed_amount) ?? NaN) > 0)) {
      errors['fixed_amount'] = '請輸入大於 0 的金額';
    }
    const integers: ('post_month_offset' | 'post_day' | 'delay_days')[] = form.posting === 'after_window'
      ? ['post_month_offset', 'post_day']
      : form.posting === 'after_transaction' ? ['delay_days'] : [];
    for (const key of integers) {
      if (!isInteger(form[key])) {
        errors[key] = '請輸入整數';
      }
    }
    if (form.starts_on && form.ends_on && form.starts_on > form.ends_on) {
      errors['ends_on'] = '迄日不可早於起日';
    }
    return errors;
  }

  private toInput(): RewardRuleInput {
    const form = this.form();
    const body: RewardRuleInput = {
      name: form.name.trim(),
      method: form.method,
      rate: form.method === 'percent' ? decimalOrNull(form.rate) : null,
      fixed_amount: form.method === 'fixed' ? decimalOrNull(form.fixed_amount) : null,
      posting: form.posting,
      delay_days: form.posting === 'after_transaction' ? Number(form.delay_days.trim()) : 0,
      post_month_offset: form.posting === 'after_window' ? Number(form.post_month_offset.trim()) : 0,
      post_day: form.posting === 'after_window' ? Number(form.post_day.trim()) : 1,
      txn_rounding: form.txn_rounding,
      total_rounding: form.total_rounding,
      total_cap: decimalOrNull(form.total_cap),
      is_basic: form.is_basic,
      reward_account_id: form.reward_account_id,
      reward_project_id: form.reward_project_id,
      starts_on: form.starts_on || null,
      ends_on: form.ends_on || null,
      is_enabled: form.is_enabled,
      description: form.description.trim() || null,
    };
    if (this.shareTouched) {
      body.share_cap_with = form.share_cap_with;
    }
    return body;
  }

  private blankForm(): RuleForm {
    return {
      name: '', method: 'percent', rate: '', fixed_amount: '', posting: 'after_window', delay_days: '0',
      post_month_offset: '0', post_day: '1', txn_rounding: 'keep', total_rounding: 'keep', total_cap: '',
      share_cap_with: null, is_basic: false, reward_account_id: null, reward_project_id: null, starts_on: '',
      ends_on: '', is_enabled: true, description: '',
    };
  }

  private dialog(): HTMLElement | null {
    return this.host.nativeElement.querySelector<HTMLElement>('.sheet');
  }
}
