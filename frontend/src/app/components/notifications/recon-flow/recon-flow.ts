import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  linkedSignal,
  output,
  signal,
  untracked,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { NotificationCenterService } from '../../../services/notification-center.service';
import { injectToasts } from '../../../services/toast.service';
import { BannerComponent } from '../../ui/banner/banner';
import { isHandledKey } from '../../accounting/accounting-ui';
import { formatMoney } from '../../accounting/format';
import {
  INBOX_GENERIC_ERROR,
  InboxCard,
  buttonLabel,
  inboxErrorMessage,
  inboxLearn,
  learnLabel,
  learnableAnswer,
  lineKind,
  matchAnswer,
  recordAnswer,
} from '../../accounting/inbox/inbox-view';
import {
  InboxAccount,
  InboxApplyBody,
  InboxApplyOut,
  InboxButton,
  InboxItem,
  InboxOut,
} from '../../accounting/inbox/inbox.model';
import { InboxService } from '../../accounting/inbox/inbox.service';
import {
  AppInboxMatchSheetComponent,
  InboxMatchResult,
} from '../../accounting/inbox/match-sheet/match-sheet';
import {
  AppInboxRecordSheetComponent,
  InboxRecordResult,
} from '../../accounting/inbox/record-sheet/record-sheet';
import { INBOX_READ_LIMIT } from '../sources/recon';
import {
  FlowState,
  advance,
  afterRead,
  askItems,
  followers,
  goTo,
  move,
  progress,
  resume,
  skip,
  startFlow,
} from './recon-flow-state';
import { compareRows, flowCard, flowChip, flowEntry, flowHeadline, flowLearnable, flowNote } from './recon-flow-view';
import { ReconUndoService } from './recon-undo.service';

/** Above this many cases the bar is one plain fill instead of one segment per case. */
const MAX_SEGMENTS = 20;

const FAILED_PREFIX = '沒成功：';
const GENERIC_PLAIN = INBOX_GENERIC_ERROR.replace(/^沒成功，/, '');

/** As the inbox panel: the mapped message without the `沒成功：` / `沒成功，` that `inboxErrorMessage` may lead with. */
function plainError(codeOrErr: unknown): string {
  const message = inboxErrorMessage(codeOrErr);
  if (message === INBOX_GENERIC_ERROR) {
    return GENERIC_PLAIN;
  }
  return message.startsWith(FAILED_PREFIX) ? message.slice(FAILED_PREFIX.length) : message;
}

/** ARIA text widgets whose letters are input (as the shell's global keys). */
const TYPING_ROLES = '[role="combobox"], [role="listbox"], [role="spinbutton"]';
const TYPING_TAGS: ReadonlySet<string> = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const OPEN_DIALOG = '[role="dialog"], [aria-modal="true"]';

function typing(node: Element | null): boolean {
  if (!(node instanceof HTMLElement)) {
    return false;
  }
  return TYPING_TAGS.has(node.tagName) || node.isContentEditable || !!node.closest(TYPING_ROLES);
}

/** `2026-09-30` → `9/30`. */
function monthDay(iso: string): string {
  return `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
}

type OneTap = Exclude<InboxButton, 'record' | 'match_candidate' | 'apply_proposal' | 'reject_proposal'>;

/** An answer the server took (or refused) whose re-read has not landed yet. */
interface PendingAnswer {
  readonly caseId: number;
  readonly answer: string;
  readonly out: InboxApplyOut;
  readonly rulePattern: string | null;
}

type SegmentState = 'done' | 'current' | 'rest';

/**
 * 對帳 one question at a time for one statement (spec D5): reads the inbox, shows the statement's open ask items as
 * steps, applies one answer per press through `/inbox/apply` (the inbox panel's body shape, one case), re-reads with
 * the account id and moves on. 記為… / 對到… open the panel's record / match sheets.
 */
@Component({
  selector: 'app-recon-flow',
  standalone: true,
  imports: [
    NgTemplateOutlet,
    RouterLink,
    BannerComponent,
    AppInboxRecordSheetComponent,
    AppInboxMatchSheetComponent,
  ],
  templateUrl: './recon-flow.html',
  styleUrl: './recon-flow.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'region',
    'aria-label': '對帳',
    tabindex: '-1',
    '[class.flow--panes]': 'panes()',
    '(document:keydown)': 'onKey($event)',
  },
})
export class ReconFlowComponent {
  private readonly inbox = inject(InboxService);
  private readonly accounting = inject(AccountingService);
  private readonly center = inject(NotificationCenterService);
  private readonly undo = inject(ReconUndoService);
  private readonly layout = inject(LayoutModeService);
  private readonly toasts = injectToasts();
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);

  readonly statementId = input.required<number>();
  readonly closed = output<void>();

  readonly panes = computed(() => this.layout.mode() === 'panes');

  readonly load = signal<'loading' | 'failed' | 'ready'>('loading');
  readonly account = signal<InboxAccount | null>(null);
  private readonly periodEnd = signal<string | null>(null);
  readonly flow = signal<FlowState>(startFlow([]));
  /** From a press until its re-read settles (or the apply fails). */
  readonly busy = signal(false);
  /** The answer went through but the re-read after it failed: only 重試 (a read) is offered. */
  readonly rereadFailed = signal(false);
  readonly locked = computed(() => this.busy() || this.rereadFailed());

  readonly recordCard = signal<InboxCard | null>(null);
  readonly matchCard = signal<InboxCard | null>(null);

  private readonly cursor = computed(() => this.flow().cursor);
  /** Off on every step change. */
  readonly learnOn = linkedSignal<number | null, boolean>({ source: this.cursor, computation: () => false });
  /** `沒成功：…` under the answers; cleared on every step change. */
  readonly inlineError = linkedSignal<number | null, string | null>({ source: this.cursor, computation: () => null });

  private gen = 0;
  private reading: Subscription | null = null;
  private pending: PendingAnswer | null = null;
  private pendingGoTo: number | null = null;
  private destroyed = false;

  readonly item = computed<InboxItem | null>(() => {
    const cursor = this.cursor();
    return cursor === null ? null : (this.flow().open.get(cursor) ?? null);
  });
  private readonly currency = computed(() => this.account()?.currency ?? 'TWD');
  readonly progressInfo = computed(() => progress(this.flow()));

  readonly kicker = computed(() => {
    const account = this.account();
    return account ? `對帳 · ${account.name}` : '對帳';
  });
  readonly titleText = computed(() => {
    const end = this.periodEnd();
    return end ? `至 ${monthDay(end)} 帳單` : null;
  });
  readonly subText = computed(() => {
    const task = this.center.tasks().find((t) => t.id === `recon:${this.statementId()}`);
    const decide = `這次要你決定 ${this.progressInfo().total} 筆`;
    return task?.progress ? `已對上 ${task.progress.k} / ${task.progress.n} 行 · ${decide}` : decide;
  });
  readonly statementLink = computed(() => {
    const account = this.account();
    return account ? `/accounting/accounts/${account.id}/statements/${this.statementId()}` : null;
  });

  /** One state per case up to MAX_SEGMENTS; null above it (a plain bar). */
  readonly segments = computed<SegmentState[] | null>(() => {
    const state = this.flow();
    if (state.order.length > MAX_SEGMENTS) {
      return null;
    }
    return state.order.map((id) => (!state.open.has(id) ? 'done' : id === state.cursor ? 'current' : 'rest'));
  });
  readonly barColumns = computed(() => `repeat(${this.flow().order.length}, 1fr)`);
  readonly handledPercent = computed(() => {
    const { handled, total } = this.progressInfo();
    return total === 0 ? 0 : (handled / total) * 100;
  });
  readonly progressText = computed(() => {
    const { position, total, handled } = this.progressInfo();
    if (this.cursor() === null) {
      return `${handled} / ${total} 筆`;
    }
    return this.panes() ? `第 ${position} / ${total} 筆` : `${position} / ${total}`;
  });

  readonly step = computed(() => {
    const item = this.item();
    if (!item) {
      return null;
    }
    const currency = this.currency();
    const hint = item.rule_hint;
    return {
      item,
      chip: flowChip(item.question),
      headline: flowHeadline(item, currency),
      rows: compareRows(item, currency),
      pair: this.pairNote(item),
      note: flowNote(item, currency),
      highlight: !item.part_of?.length,
      answers: item.actions.map((action) => ({ action, label: buttonLabel(action, item, currency) })),
      learn:
        hint && flowLearnable(item)
          ? learnLabel(hint, item.actions.find((a) => learnableAnswer(item.question, a)) ?? 'dismiss')
          : null,
    };
  });

  readonly endTitle = computed(() => {
    const { handled, remaining, total } = this.progressInfo();
    if (total === 0) {
      return '這張帳單沒有要你決定的了';
    }
    return remaining > 0 ? `已處理 ${handled} 筆，還有 ${remaining} 筆` : '這張帳單都決定好了';
  });

  constructor() {
    effect(() => {
      this.statementId();
      untracked(() => this.restart());
    });

    let lastSeq = untracked(this.undo.reverted)?.seq ?? 0;
    effect(() => {
      const reverted = this.undo.reverted();
      if (!reverted || reverted.seq <= lastSeq) {
        return;
      }
      lastSeq = reverted.seq;
      untracked(() => this.onReverted(reverted.caseId));
    });

    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      this.reading?.unsubscribe();
    });
  }

  // ----- owner actions -------------------------------------------------------------------------------------------

  press(action: InboxButton): void {
    const item = this.item();
    const account = this.account();
    if (!item || !account || this.locked()) {
      return;
    }
    switch (action) {
      case 'record':
        this.recordCard.set(flowCard(item, account));
        return;
      case 'match_candidate':
        this.matchCard.set(flowCard(item, account));
        return;
      case 'apply_proposal':
      case 'reject_proposal':
        return;
    }
    this.applyOneTap(item, action);
  }

  record(card: InboxCard, result: InboxRecordResult): void {
    this.recordCard.set(null);
    const item = card.items[0];
    const params: Record<string, unknown> = { category_id: result.category_id };
    if (result.name !== null) {
      params['name'] = result.name;
    }
    if (result.project_id !== null) {
      params['project_id'] = result.project_id;
    }
    this.send(item, {
      cases: [{ case_id: item.case_id, case_version: item.case_version }],
      action: 'record',
      params,
      answer: recordAnswer(lineKind(item), result.category_name),
      learn: result.learn,
    });
  }

  match(card: InboxCard, result: InboxMatchResult): void {
    this.matchCard.set(null);
    const item = card.items[0];
    this.send(item, {
      cases: [{ case_id: item.case_id, case_version: item.case_version }],
      action: 'match_candidate',
      params: { entry_ids: result.entry_ids },
      answer: matchAnswer(result.titles[String(item.case_id)]),
      learn: null,
    });
  }

  toggleLearn(): void {
    if (!this.locked()) {
      this.learnOn.update((on) => !on);
    }
  }

  skipStep(): void {
    if (!this.locked()) {
      this.owned((state) => skip(state));
    }
  }

  moveStep(delta: -1 | 1): void {
    if (!this.locked()) {
      this.owned((state) => move(state, delta));
    }
  }

  resumeFlow(): void {
    if (!this.locked()) {
      this.owned((state) => resume(state));
    }
  }

  retryLoad(): void {
    this.restart();
  }

  /** 重試 after a failed re-read: a read only, never the answer again. */
  retryRead(): void {
    if (this.busy()) {
      return;
    }
    this.rereadFailed.set(false);
    this.busy.set(true);
    this.read(false);
  }

  onKey(event: KeyboardEvent): void {
    if (isHandledKey(event) || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    if (this.busy() || this.recordCard() || this.matchCard()) {
      return;
    }
    if (this.document.querySelector(OPEN_DIALOG) || typing(this.document.activeElement)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closed.emit();
      return;
    }
    if (this.load() !== 'ready') {
      return;
    }
    const step = this.step();
    switch (event.key) {
      case '1':
      case '2':
      case '3': {
        const answer = step?.answers[Number(event.key) - 1];
        if (!answer) {
          return;
        }
        event.preventDefault();
        this.press(answer.action);
        return;
      }
      case 's':
      case 'S':
        if (!step) {
          return;
        }
        event.preventDefault();
        this.skipStep();
        return;
      case 'ArrowLeft':
      case 'ArrowRight':
        if (!step) {
          return;
        }
        event.preventDefault();
        this.moveStep(event.key === 'ArrowLeft' ? -1 : 1);
        return;
    }
  }

  // ----- reading ---------------------------------------------------------------------------------------------------

  private restart(): void {
    this.gen += 1;
    this.pending = null;
    this.pendingGoTo = null;
    this.account.set(null);
    this.periodEnd.set(null);
    this.flow.set(startFlow([]));
    this.busy.set(false);
    this.rereadFailed.set(false);
    this.recordCard.set(null);
    this.matchCard.set(null);
    this.load.set('loading');
    this.read(true);
  }

  /** One read in flight: a newer read cancels the older one (its answer would be stale). */
  private read(initial: boolean): void {
    this.reading?.unsubscribe();
    const statementId = this.statementId();
    const accountId = this.account()?.id;
    const source =
      initial || accountId === undefined
        ? this.inbox.get({ limit: INBOX_READ_LIMIT })
        : this.inbox.get({ accountId });
    this.reading = source.subscribe({
      next: (data) => {
        this.reading = null;
        if (initial) {
          this.started(data, statementId);
        } else {
          this.landed(data, statementId, accountId);
        }
      },
      error: () => {
        this.reading = null;
        if (initial) {
          this.load.set('failed');
          return;
        }
        this.busy.set(false);
        this.pendingGoTo = null;
        if (this.pending) {
          this.rereadFailed.set(true);
        }
      },
    });
  }

  private started(data: InboxOut, statementId: number): void {
    const account =
      data.accounts.find((a) => a.items.some((item) => item.statement.id === statementId)) ?? null;
    const items = account ? askItems(account.items, statementId) : [];
    this.account.set(account);
    this.periodEnd.set(account?.items.find((item) => item.statement.id === statementId)?.statement.period_end ?? null);
    this.flow.set(startFlow(items));
    this.load.set('ready');
  }

  private landed(data: InboxOut, statementId: number, accountId: number | undefined): void {
    const items = data.accounts.find((a) => a.id === accountId)?.items ?? [];
    const before = this.flow().cursor;
    let state = afterRead(this.flow(), askItems(items, statementId));
    const pending = this.pending;
    this.pending = null;
    let error: string | null = null;
    if (pending) {
      error = this.settle(pending, state);
    }
    if (state.cursor !== null && !state.open.has(state.cursor)) {
      state = advance(state);
    }
    if (this.pendingGoTo !== null) {
      state = goTo(state, this.pendingGoTo);
      this.pendingGoTo = null;
    }
    const focus = pending !== null || this.hasFocus();
    this.flow.set(state);
    if (pending) {
      this.inlineError.set(error);
    }
    this.busy.set(false);
    this.rereadFailed.set(false);
    if (focus && state.cursor !== before) {
      this.focusStep();
    }
  }

  /** The answered case left: 完成 with 復原 (or the rule error). Still open: the refusal text. */
  private settle(pending: PendingAnswer, state: FlowState): string | null {
    const result = pending.out.results.find((r) => r.case_id === pending.caseId);
    if (state.open.has(pending.caseId)) {
      return FAILED_PREFIX + plainError(result?.code ?? null);
    }
    if (!result || result.status === 'refused') {
      return null;
    }
    if (pending.out.rule_error) {
      this.toasts.error('答案已記下，但規則沒存：' + plainError(pending.out.rule_error));
      return null;
    }
    const actionIds = pending.out.results
      .filter((r) => r.status !== 'refused' && r.action_id !== null)
      .map((r) => r.action_id as number);
    this.undo.offer(
      pending.answer,
      pending.caseId,
      actionIds,
      pending.out.rule_id !== null ? pending.rulePattern : null,
    );
    return null;
  }

  private onReverted(caseId: number): void {
    if (this.load() !== 'ready') {
      return; // the first read (or its 重試) brings the case back anyway
    }
    this.pendingGoTo = caseId;
    if (this.busy()) {
      return; // the answer's own re-read lands after the revert and goes to the case
    }
    this.read(false);
  }

  // ----- applying --------------------------------------------------------------------------------------------------

  private applyOneTap(item: InboxItem, action: OneTap): void {
    const learnable = item.rule_hint !== null && learnableAnswer(item.question, action);
    this.send(item, {
      cases: [{ case_id: item.case_id, case_version: item.case_version }],
      action,
      params: {},
      answer: buttonLabel(action, item, this.currency()),
      learn: learnable ? inboxLearn(this.learnOn(), false, action) : null,
    });
  }

  /**
   * One `/inbox/apply`. Not tied to the component's life: a request cancelled on close could still have been applied,
   * so it runs to its end and bumps `entriesChanged`; only the screen work is skipped once the flow is gone.
   */
  private send(item: InboxItem, body: InboxApplyBody): void {
    if (this.locked()) {
      return;
    }
    const gen = this.gen;
    this.busy.set(true);
    this.inlineError.set(null);
    this.inbox.apply(body).subscribe({
      next: (out) => {
        this.accounting.markEntriesChanged();
        if (this.destroyed || gen !== this.gen) {
          return;
        }
        this.pending = {
          caseId: item.case_id,
          answer: body.answer,
          out,
          rulePattern: item.rule_hint?.merchant_pattern ?? null,
        };
        this.read(false);
      },
      error: (err: unknown) => {
        if (this.destroyed || gen !== this.gen) {
          return;
        }
        this.busy.set(false);
        this.inlineError.set(FAILED_PREFIX + plainError(err));
      },
    });
  }

  // ----- helpers ---------------------------------------------------------------------------------------------------

  /** A step change the owner asked for in the flow: focus follows it. */
  private owned(change: (state: FlowState) => FlowState): void {
    const before = this.flow();
    const after = change(before);
    if (after === before) {
      return;
    }
    this.flow.set(after);
    if (after.cursor !== before.cursor) {
      this.focusStep();
    }
  }

  private pairNote(item: InboxItem): string | null {
    const state = this.flow();
    const follower = [...followers(state)].find(([, leader]) => leader === item.case_id)?.[0];
    const entry = follower === undefined ? null : flowEntry(state.open.get(follower) as InboxItem);
    if (!entry) {
      return null;
    }
    const amount = formatMoney(entry.amount, this.currency(), { sign: true });
    return `你這筆 ${amount} 也出現在「你記了，銀行沒有」，選了就一起處理。`;
  }

  private hasFocus(): boolean {
    return this.host.nativeElement.contains(this.document.activeElement);
  }

  /** The new question's headline (or the end state's heading), so a screen reader reads it. */
  private focusStep(): void {
    afterNextRender(
      () => this.host.nativeElement.querySelector<HTMLElement>('h2[tabindex="-1"]')?.focus(),
      { injector: this.injector },
    );
  }
}
