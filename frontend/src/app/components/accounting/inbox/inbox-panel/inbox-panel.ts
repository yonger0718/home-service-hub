import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  linkedSignal,
  output,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { catchError, concatMap, defer, from, map, of } from 'rxjs';

import { slashDate } from '../../dates';
import { formatMoney } from '../../format';
import {
  AMOUNT_ANY_LABEL,
  InboxCard,
  buildCards,
  buttonLabel,
  cardTitle,
  inboxErrorMessage,
  learnLabel,
  questionText,
} from '../inbox-view';
import {
  InboxAccount,
  InboxApplyBody,
  InboxApplyOut,
  InboxButton,
  InboxItem,
  InboxLearn,
  InboxOut,
} from '../inbox.model';
import { InboxService } from '../inbox.service';

/** Ask cards shown on 全部 before the 還有 N 張 → link. */
export const INBOX_PREVIEW_CARDS = 3;

/** The answers a rule can be learned from (spec §6.1); any other button sends `learn: null`. */
const LEARNABLE: readonly InboxButton[] = ['record', 'dismiss', 'accept_bank_amount'];

/** Buttons the panel applies at once (no sheet). */
type OneTap = 'dismiss' | 'accept_bank_amount' | 'defer' | 'anchor';

type FoldKey = 'proposed' | 'balanceGap' | 'settlement' | 'other';

/** The buttons a folded section offers, whatever else the case allows (spec §7.2). */
const FOLD_BUTTONS: Record<FoldKey, readonly InboxButton[]> = {
  proposed: ['apply_proposal', 'reject_proposal'],
  balanceGap: ['anchor', 'dismiss'],
  settlement: [],
  other: ['dismiss'],
};

/**
 * The `learn` of an apply request (spec §6.1 / §7.2): null when 以後都這樣 is unticked; else the line's amount is kept
 * unless 金額不同也算 is ticked — and never for 改成銀行的數, whose rule has no amount.
 */
export function inboxLearn(ticked: boolean, amountAny: boolean, button: InboxButton): InboxLearn {
  return ticked ? { amount_exact: button === 'accept_bank_amount' ? false : !amountAny } : null;
}

/** `沒成功：…` for a refusal code or a failed request (no doubled prefix for an unknown code). */
function refusedText(codeOrErr: unknown): string {
  const message = inboxErrorMessage(codeOrErr);
  return message.startsWith('沒成功：') ? message : `沒成功：${message}`;
}

/** Keeps only the refusals whose case is still in `data` (a case that left the inbox loses its message). */
function keepPresent(
  previous: ReadonlyMap<number, string> | undefined,
  data: InboxOut,
): ReadonlyMap<number, string> {
  if (!previous || previous.size === 0) {
    return new Map();
  }
  const present = new Set(
    data.accounts.flatMap((account) => account.items.map((item) => item.case_id)),
  );
  return new Map([...previous].filter(([caseId]) => present.has(caseId)));
}

export interface InboxLearnView {
  label: string;
  ticked: boolean;
  /** 金額不同也算: a single-item card that can be recorded or dismissed. */
  amountOption: boolean;
  amountAny: boolean;
  /** `同金額 ×N` on a merged card, whose amount is locked exact. */
  lockedCaption: string | null;
}

export interface InboxCardView {
  card: InboxCard;
  title: string;
  question: string;
  /** Merged cards: `2026/09/03 −$120` per member. */
  members: string[];
  buttons: { action: InboxButton; label: string }[];
  learn: InboxLearnView | null;
  refused: string[];
  busy: boolean;
}

export interface InboxFoldView {
  key: FoldKey;
  title: string;
  views: InboxCardView[];
}

/**
 * 待處理 (spec §7.2, option A): the cards that need the owner's answer per account, then the folded sections.
 * One-tap answers post `/inbox/apply` here; 記為… and 對到… are handed to the parent's sheets.
 */
@Component({
  selector: 'app-inbox-panel',
  standalone: true,
  imports: [NgTemplateOutlet, RouterLink],
  templateUrl: './inbox-panel.html',
  styleUrls: ['./inbox-panel.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppInboxPanelComponent {
  private readonly inbox = inject(InboxService);

  readonly data = input.required<InboxOut>();
  readonly mode = input<'full' | 'preview'>('full');
  /** After every write that changed something: the parent reloads `data`. */
  readonly changed = output<void>();
  readonly openRecord = output<InboxCard>();
  readonly openMatch = output<InboxCard>();

  readonly amountAnyLabel = AMOUNT_ANY_LABEL;

  /** Cases answered since the last `data`: they leave at once, before the parent's reload lands. */
  private readonly gone = linkedSignal<InboxOut, ReadonlySet<number>>({
    source: this.data,
    computation: () => new Set(),
  });
  /**
   * `沒成功：…` per case id. Survives a new `data`; cleared when the case applies or leaves `data`. A linkedSignal only
   * recomputes when read, so the template reads it on every render (`data-refused`): each rendered `data` prunes it.
   */
  readonly refused = linkedSignal<InboxOut, ReadonlyMap<number, string>>({
    source: this.data,
    computation: (data, previous) => keepPresent(previous?.value, data),
  });
  readonly ruleError = signal<string | null>(null);
  /** `套用中 k/n` / `退回中 k/n` while proposals are worked through. */
  readonly progress = signal<string | null>(null);
  /** The merged card whose 忽略 waits for 確定. */
  readonly confirming = signal<string | null>(null);
  private readonly busy = signal<ReadonlySet<string>>(new Set());
  /** Explicit learn ticks per card key; untouched cards use the default (ticked only when merged). */
  private readonly learnTicks = signal<ReadonlyMap<string, boolean>>(new Map());
  private readonly amountAnyTicks = signal<ReadonlyMap<string, boolean>>(new Map());

  readonly cards = computed(() => {
    const gone = this.gone();
    return buildCards(
      this.data().accounts.map((account) => ({
        ...account,
        items: account.items.filter((item) => !gone.has(item.case_id)),
      })),
    );
  });
  readonly headText = computed(() => {
    const counts = this.data().counts;
    return `要你決定 ${counts.ask} · 其他 ${counts.proposed + counts.balance_gap + counts.settlement + counts.other}`;
  });
  private readonly shownAsk = computed(() =>
    this.mode() === 'preview' ? this.cards().ask.slice(0, INBOX_PREVIEW_CARDS) : this.cards().ask,
  );
  readonly moreCount = computed(() =>
    this.mode() === 'preview' ? Math.max(0, this.cards().ask.length - INBOX_PREVIEW_CARDS) : 0,
  );
  /** Ask cards per account, in the server's order. */
  readonly askGroups = computed(() => {
    const groups: { account: InboxAccount; icon: string; views: InboxCardView[] }[] = [];
    for (const card of this.shownAsk()) {
      const last = groups.at(-1);
      const view = this.view(card, card.actions);
      if (last && last.account.id === card.account.id) {
        last.views.push(view);
      } else {
        groups.push({
          account: card.account,
          icon: card.account.is_credit ? '💳' : '🏦',
          views: [view],
        });
      }
    }
    return groups;
  });
  readonly folds = computed<InboxFoldView[]>(() => {
    const cards = this.cards();
    const count = (list: InboxCard[]) => list.reduce((sum, card) => sum + card.items.length, 0);
    const folds: InboxFoldView[] = [
      {
        key: 'proposed',
        title: `系統有提議 ${count(cards.proposed)}（全部套用）`,
        views: this.foldViews('proposed', cards.proposed),
      },
      {
        key: 'balanceGap',
        title: `期初差額 ${count(cards.balanceGap)}`,
        views: this.foldViews('balanceGap', cards.balanceGap),
      },
      {
        key: 'settlement',
        title: `國泰交割款 ${count(cards.settlement)} — 等證券連動`,
        views: this.foldViews('settlement', cards.settlement),
      },
      {
        key: 'other',
        title: `其他 ${count(cards.other)}`,
        views: this.foldViews('other', cards.other),
      },
    ];
    return folds.filter((fold) => fold.views.length > 0);
  });

  tap(card: InboxCard, button: InboxButton): void {
    switch (button) {
      case 'record':
        this.confirming.set(null);
        this.openRecord.emit(card);
        return;
      case 'match_candidate':
        this.confirming.set(null);
        this.openMatch.emit(card);
        return;
      case 'apply_proposal':
      case 'reject_proposal':
        this.runProposals([card], button);
        return;
    }
    if (button === 'dismiss' && card.items.length > 1 && this.confirming() !== card.key) {
      this.confirming.set(card.key);
      return;
    }
    this.confirming.set(null);
    this.applyOneTap(card, button);
  }

  /** 全部套用: every proposal in the section, one by one. */
  applyAll(): void {
    this.runProposals(this.cards().proposed, 'apply_proposal');
  }

  setLearn(card: InboxCard, ticked: boolean): void {
    this.learnTicks.update((map) => new Map(map).set(card.key, ticked));
  }

  setAmountAny(card: InboxCard, ticked: boolean): void {
    this.amountAnyTicks.update((map) => new Map(map).set(card.key, ticked));
  }

  /** The learn the card's controls ask for with this button (null when it cannot or should not teach). */
  learnFor(card: InboxCard, button: InboxButton): InboxLearn {
    if (!card.learnable || !LEARNABLE.includes(button)) {
      return null;
    }
    return inboxLearn(
      this.learnTicked(card),
      card.items.length === 1 && this.amountAnyTicked(card),
      button,
    );
  }

  private learnTicked(card: InboxCard): boolean {
    return this.learnTicks().get(card.key) ?? card.items.length > 1;
  }

  private amountAnyTicked(card: InboxCard): boolean {
    return this.amountAnyTicks().get(card.key) ?? false;
  }

  private applyOneTap(card: InboxCard, action: OneTap): void {
    this.submit(card, {
      cases: card.items.map((item) => ({ case_id: item.case_id, case_version: item.case_version })),
      action,
      params: {},
      answer: buttonLabel(action, card.items[0], card.account.currency),
      learn: this.learnFor(card, action),
    });
  }

  /** One `/inbox/apply` for the card: applied members leave, refused ones stay with their reason. */
  private submit(card: InboxCard, body: InboxApplyBody): void {
    if (this.busy().has(card.key)) {
      return;
    }
    this.setBusy(card.key, true);
    this.ruleError.set(null);
    this.inbox.apply(body).subscribe({
      next: (out) => {
        this.setBusy(card.key, false);
        this.settle(out);
        this.changed.emit();
      },
      error: (err: unknown) => {
        this.setBusy(card.key, false);
        this.markRefused(
          card.items.map((item) => item.case_id),
          refusedText(err),
        );
      },
    });
  }

  private settle(out: InboxApplyOut): void {
    this.markDone(
      out.results.filter((result) => result.status !== 'refused').map((result) => result.case_id),
    );
    for (const result of out.results.filter((result) => result.status === 'refused')) {
      this.markRefused([result.case_id], refusedText(result.code));
    }
    this.ruleError.set(out.rule_error ? `規則沒存：${inboxErrorMessage(out.rule_error)}` : null);
  }

  /** Proposals are few: one request at a time with `套用中 k/n`, carrying on past a refusal. */
  private runProposals(cards: InboxCard[], button: 'apply_proposal' | 'reject_proposal'): void {
    if (this.progress() !== null) {
      return;
    }
    const items = cards
      .flatMap((card) => card.items)
      .filter(
        (item): item is InboxItem & { proposal: NonNullable<InboxItem['proposal']> } =>
          item.proposal !== null,
      );
    if (items.length === 0) {
      return;
    }
    const verb = button === 'apply_proposal' ? '套用中' : '退回中';
    let succeeded = false;
    from(items)
      .pipe(
        concatMap((item, index) =>
          defer(() => {
            this.progress.set(`${verb} ${index + 1}/${items.length}`);
            const call =
              button === 'apply_proposal'
                ? this.inbox.applyProposal(item.proposal.id)
                : this.inbox.rejectProposal(item.proposal.id);
            return call.pipe(
              map((): string | null => null),
              catchError((err: unknown) => of(refusedText(err))),
              map((error) => ({ item, error })),
            );
          }),
        ),
      )
      .subscribe({
        next: ({ item, error }) => {
          if (error === null) {
            succeeded = true;
            this.markDone([item.case_id]);
          } else {
            this.markRefused([item.case_id], error);
          }
        },
        complete: () => {
          this.progress.set(null);
          if (succeeded) {
            this.changed.emit();
          }
        },
      });
  }

  private markDone(caseIds: number[]): void {
    if (caseIds.length === 0) {
      return;
    }
    this.gone.update((set) => new Set([...set, ...caseIds]));
    this.refused.update((map) => new Map([...map].filter(([caseId]) => !caseIds.includes(caseId))));
  }

  private markRefused(caseIds: number[], text: string): void {
    this.refused.update((map) => {
      const next = new Map(map);
      caseIds.forEach((caseId) => next.set(caseId, text));
      return next;
    });
  }

  private setBusy(key: string, on: boolean): void {
    this.busy.update((set) => {
      const next = new Set(set);
      if (on) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  }

  private foldViews(key: FoldKey, cards: InboxCard[]): InboxCardView[] {
    return cards.map((card) =>
      this.view(
        card,
        card.actions.filter((action) => FOLD_BUTTONS[key].includes(action)),
      ),
    );
  }

  private view(card: InboxCard, actions: readonly InboxButton[]): InboxCardView {
    const currency = card.account.currency;
    const first = card.items[0];
    const merged = card.items.length > 1;
    const refused = this.refused();
    return {
      card,
      title: cardTitle(card, currency),
      question: questionText(first, currency),
      members: merged
        ? card.items.map((item) => {
            const source = item.line ?? item.entry;
            return source
              ? `${slashDate(source.date)} ${formatMoney(source.amount, currency)}`
              : `#${item.case_id}`;
          })
        : [],
      buttons: actions.map((action) => ({
        action,
        label:
          action === 'dismiss' && this.confirming() === card.key
            ? '確定'
            : buttonLabel(action, first, currency),
      })),
      learn:
        card.learnable && card.rule_hint
          ? {
              label: learnLabel(
                card.rule_hint,
                card.actions.find((action) => LEARNABLE.includes(action)) ?? 'dismiss',
              ),
              ticked: this.learnTicked(card),
              amountOption:
                !merged &&
                card.actions.some((action) => action === 'record' || action === 'dismiss'),
              amountAny: this.amountAnyTicked(card),
              lockedCaption: merged ? `同金額 ×${card.items.length}` : null,
            }
          : null,
      refused: [
        ...new Set(
          card.items
            .map((item) => refused.get(item.case_id))
            .filter((text): text is string => !!text),
        ),
      ],
      busy: this.busy().has(card.key) || this.progress() !== null,
    };
  }
}
